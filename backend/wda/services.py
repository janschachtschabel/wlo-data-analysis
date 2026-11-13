"""Langlebige Objekte der App: HTTP-Client, Aggregat-Cache, Clients und Vokabulare je Instanz."""

from __future__ import annotations

import httpx

from .cache import TTLCache
from .config import Settings
from .edusharing.client import EduSharingClient
from .edusharing.vocab import VocabStore
from .instances import InstanceRegistry


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
                self._instances.get(instance_id),
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
