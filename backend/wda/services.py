"""Langlebige Objekte der App: HTTP-Client, Aggregat-Cache, Clients und Vokabulare je Instanz."""

from __future__ import annotations

import contextlib
import json
import logging
from datetime import UTC, datetime

import httpx
from pydantic import ValidationError

from .cache import TTLCache
from .config import ConfigError, Settings
from .edusharing.client import EduSharingClient
from .edusharing.vocab import VocabStore
from .external.oersi import OersiClient
from .instances import Instance, InstanceRegistry
from .store.db import Database
from .store.definitions import DefinitionStore
from .store.runner import Runner, backfill_points
from .store.transfer import Transfer

log = logging.getLogger(__name__)


class Services:
    def __init__(
        self,
        settings: Settings,
        instances: InstanceRegistry,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._settings = settings
        self._instances = instances
        self._transport = transport
        self._http: httpx.AsyncClient | None = None
        self.cache = TTLCache(ttl=settings.cache_ttl)
        self._clients: dict[str, EduSharingClient] = {}
        self._vocabs: dict[str, VocabStore] = {}
        self._db: Database | None = None
        self._stored_loaded = False

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
    def db(self) -> Database:
        """Wird beim ersten Zugriff geöffnet; wirft ``ConfigError``, wenn das nicht geht."""
        if self._db is None:
            self._db = Database(self._settings.data_dir / "store.sqlite3")
            try:  # ältere Läufe bekommen einmalig ihre Punkte für die Historie
                backfill_points(self._db)
            except Exception:
                log.exception("Punkte älterer Läufe ließen sich nicht nachtragen")
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
        if self._http is not None:
            await self._http.aclose()
            self._http = None
        if self._db is not None:
            self._db.close()
            self._db = None
