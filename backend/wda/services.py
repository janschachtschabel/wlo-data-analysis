"""Langlebige Objekte der App: HTTP-Client, Aggregat-Cache, Clients und Vokabulare je Instanz."""

from __future__ import annotations

import contextlib
import json
import logging
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import httpx
from pydantic import ValidationError

from .cache import TTLCache
from .config import ConfigError, Settings
from .edusharing.client import EduSharingClient
from .edusharing.vocab import VocabStore
from .external.oersi import OersiClient
from .instances import Instance, InstanceRegistry
from .jobs import Jobs
from .llm import config as llm_config
from .llm.client import LlmClient
from .store.db import Database
from .store.definitions import DefinitionStore
from .store.history import History
from .store.runner import Runner, backfill_instances, backfill_points
from .store.schedules import ScheduleStore
from .store.transfer import Transfer

log = logging.getLogger(__name__)


class Services:
    def __init__(
        self,
        settings: Settings,
        instances: InstanceRegistry,
        transport: httpx.AsyncBaseTransport | None = None,
        llm_transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._settings = settings
        self._instances = instances
        self._transport = transport
        self._http: httpx.AsyncClient | None = None
        self._llm_transport = llm_transport
        self._llm_http: httpx.AsyncClient | None = None
        self._llm: llm_config.LlmConfig | None = None
        self.cache = TTLCache(ttl=settings.cache_ttl)
        self._clients: dict[str, EduSharingClient] = {}
        self._vocabs: dict[str, VocabStore] = {}
        self._db: Database | None = None
        self._stored_loaded = False
        self.jobs = Jobs()

    @property
    def registry(self) -> InstanceRegistry:
        """Die Instanzen samt der in der Oberfläche angelegten. Ist die Ablage nicht zu
        öffnen, bleiben die festen; der nächste Zugriff versucht es wieder."""
        if not self._stored_loaded:
            try:
                rows = self.db.read_now(lambda con: con.execute(
                    "SELECT id, data FROM instances ORDER BY rowid").fetchall())
            except ConfigError:
                return self._instances
            items = []
            for row in rows:
                try:
                    items.append(Instance.model_validate(json.loads(row["data"])))
                except (ValueError, ValidationError):
                    log.error("Gespeicherte Instanz '%s' ist unlesbar und wird übergangen.",
                              row["id"])
            self._instances.set_stored(items)
            self._stored_loaded = True
        return self._instances

    async def save_instance(self, inst: Instance) -> None:
        data = inst.model_dump(mode="json")
        data["password"] = inst.password.get_secret_value() if inst.password else None
        now = datetime.now(UTC).isoformat(timespec="seconds")
        await self.db.write(lambda con: con.execute(
            "INSERT INTO instances (id, data, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET data = excluded.data, updated_at = excluded.updated_at",
            (inst.id, json.dumps(data, ensure_ascii=False), now)))
        self.registry.put(inst)
        self.forget(inst.id)

    async def delete_instance(self, instance_id: str) -> None:
        await self.db.write(
            lambda con: con.execute("DELETE FROM instances WHERE id = ?", (instance_id,)))
        self.registry.remove(instance_id)
        self.forget(instance_id)

    def forget(self, instance_id: str) -> None:
        """Nach einer Änderung: Client, Vokabular samt Kopie auf der Platte und gemerkte
        Zählungen verwerfen, damit nichts von der alten Adresse oder Anmeldung bleibt."""
        self._clients.pop(instance_id, None)
        self._vocabs.pop(instance_id, None)
        self.cache.clear()
        with contextlib.suppress(OSError):
            (self._settings.data_dir / "cache" / f"mds-{instance_id}.json").unlink()

    @property
    def llm(self) -> llm_config.LlmConfig:
        """Die geltenden Einstellungen des Sprachmodells (Oberfläche vor Umgebung)."""
        if self._llm is None:
            try:
                row = self.db.read_now(lambda con: con.execute(
                    "SELECT value FROM meta WHERE key = ?", (llm_config.META_KEY,)).fetchone())
            except ConfigError:
                return llm_config.from_env(self._settings)
            self._llm = llm_config.from_stored(row["value"] if row else None, self._settings)
        return self._llm

    async def save_llm(self, config: llm_config.LlmConfig | None) -> None:
        """Speichert die Einstellungen; ``None`` stellt die der Umgebung wieder her."""
        def fn(con) -> None:
            if config is None:
                con.execute("DELETE FROM meta WHERE key = ?", (llm_config.META_KEY,))
            else:
                con.execute(
                    "INSERT INTO meta (key, value) VALUES (?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    (llm_config.META_KEY, llm_config.to_stored(config)))
        await self.db.write(fn)
        self._llm = None

    def llm_client(self, config: llm_config.LlmConfig | None = None) -> LlmClient:
        if self._llm_http is None:
            # Keine Weiterleitungen: Der Schlüssel bleibt bei der eingestellten Adresse.
            self._llm_http = httpx.AsyncClient(
                transport=self._llm_transport, follow_redirects=False,
                headers={"User-Agent": "wlo-data-analysis"})
        return LlmClient(self._llm_http, config or self.llm)

    @property
    def db(self) -> Database:
        """Wird beim ersten Zugriff geöffnet; wirft ``ConfigError``, wenn das nicht geht."""
        if self._db is None:
            self._db = Database(self._settings.data_dir / "store.sqlite3")
            try:  # ältere Läufe bekommen einmalig ihre Punkte für die Historie
                backfill_points(self._db)
            except Exception:
                log.exception("Punkte älterer Läufe ließen sich nicht nachtragen")
            try:
                backfill_instances(self._db)
            except Exception:
                log.exception("Instanzen älterer Läufe ließen sich nicht zuordnen")
        return self._db

    @property
    def definitions(self) -> DefinitionStore:
        return DefinitionStore(self.db)

    @property
    def transfer(self) -> Transfer:
        return Transfer(self.db)

    @property
    def runner(self) -> Runner:
        return Runner(self.db, self, keep=self._settings.runs_keep)

    @property
    def history(self) -> History:
        return History(self.db, ZoneInfo(self._settings.timezone))

    @property
    def schedules(self) -> ScheduleStore:
        return ScheduleStore(self.db, ZoneInfo(self._settings.timezone))

    @property
    def oersi(self) -> OersiClient:
        return OersiClient(self._http_client(), self.cache, self._settings.oersi_url,
                           timeout=self._settings.upstream_timeout)

    def _http_client(self) -> httpx.AsyncClient:
        if self._http is None:
            # Keine Weiterleitungen: Zugangsdaten einer Instanz bleiben bei ihrem Host.
            self._http = httpx.AsyncClient(
                transport=self._transport,
                follow_redirects=False,
                headers={"User-Agent": "wlo-data-analysis"},
            )
        return self._http

    def client(self, instance_id: str) -> EduSharingClient:
        """Wirft ``KeyError`` bei unbekannter Instanz."""
        if instance_id not in self._clients:
            self._clients[instance_id] = EduSharingClient(
                self.registry.get(instance_id),
                self._http_client(),
                self.cache,
                max_concurrency=self._settings.upstream_concurrency,
                timeout=self._settings.upstream_timeout,
            )
        return self._clients[instance_id]

    def vocab(self, instance_id: str) -> VocabStore:
        if instance_id not in self._vocabs:
            self._vocabs[instance_id] = VocabStore(
                self.client(instance_id), self._settings.data_dir, ttl=self._settings.vocab_ttl)
        return self._vocabs[instance_id]

    async def aclose(self) -> None:
        await self.jobs.aclose()
        if self._http is not None:
            await self._http.aclose()
            self._http = None
        if self._llm_http is not None:
            await self._llm_http.aclose()
            self._llm_http = None
        if self._db is not None:
            self._db.close()
            self._db = None
