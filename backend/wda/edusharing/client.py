"""Client für die edu-sharing-Suche.

Fordert grundsätzlich ``maxItems=0`` an und reicht ``nodes`` nie weiter: Diese Schicht
kann keine einzelnen Datensätze liefern, nur Zählungen und Facetten. Das gemessene
Verhalten der Schnittstelle steht in ``docs/edu-sharing-api-erkenntnisse.md``.
"""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass, replace
from typing import Any
from urllib.parse import quote

import httpx

from ..cache import TTLCache
from ..instances import Instance
from .errors import EduSharingError

CONTENT_TYPES = frozenset({"FILES", "FOLDERS", "FILES_AND_FOLDERS", "COLLECTIONS", "ALL"})
MAX_FACET_LIMIT = 100_000
_RETRY_STATUS = frozenset({502, 503, 504})
_NAME = r"([\w:.\-]{1,120})"


@dataclass(frozen=True)
class Criterion:
    property: str
    values: tuple[str, ...]


@dataclass(frozen=True)
class FacetValue:
    value: str
    count: int


@dataclass(frozen=True)
class Facet:
    property: str
    values: tuple[FacetValue, ...]
    other_count: int = 0

    @property
    def truncated(self) -> bool:
        """Die Werteliste wurde vom Repositorium gekürzt; ``other_count`` zählt den Rest."""
        return self.other_count > 0


@dataclass(frozen=True)
class Aggregate:
    total: int
    facets: dict[str, Facet]
    fetched_at: float
    cached: bool = False


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


class EduSharingClient:
    def __init__(
        self,
        instance: Instance,
        http: httpx.AsyncClient,
        cache: TTLCache,
        *,
        max_concurrency: int = 4,
        retries: int = 2,
        backoff: float = 0.5,
        timeout: float = 90.0,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.instance = instance
        self._http = http
        self._cache = cache
        self._semaphore = asyncio.Semaphore(max_concurrency)
        self._retries = retries
        self._backoff = backoff
        self._timeout = timeout
        self._sleep = sleep
        self._clock = clock
        self._auth: httpx.BasicAuth | None = None
        if instance.username and instance.password:
            self._auth = httpx.BasicAuth(instance.username, instance.password.get_secret_value())

    # ---- öffentliche Aufrufe -------------------------------------------------

    async def about(self) -> dict[str, Any]:
        data = await self._request("GET", f"{self.instance.rest_url}/_about")
        version = data.get("version") if isinstance(data, dict) else None
        if not isinstance(version, dict):
            raise EduSharingError("bad_response", "Die Instanz nennt keine Version.", 200)
        return {k: v for k, v in version.items() if isinstance(v, str | int | float)}

    async def metadataset(self) -> dict[str, Any]:
        inst = self.instance
        url = (f"{inst.rest_url}/mds/v1/metadatasets/"
               f"{quote(inst.repository, safe='')}/{quote(inst.metadataset, safe='')}")
        data = await self._request("GET", url)
        if not isinstance(data, dict):
            raise EduSharingError("bad_response", "Das Metadatenset hat ein unerwartetes Format.",
                                  200)
        return data

    async def aggregate(
        self,
        criteria: Iterable[Criterion],
        facets: Sequence[str] = (),
        *,
        facet_limit: int = 2000,
        content_type: str = "FILES",
        refresh: bool = False,
    ) -> Aggregate:
        """Gesamtzahl und Facetten für eine Kombination von Kriterien."""
        norm_criteria = self._normalize_criteria(criteria)
        norm_facets = self._normalize_facets(facets)
        if not _is_int(facet_limit) or not 1 <= facet_limit <= MAX_FACET_LIMIT:
            raise EduSharingError(
                "invalid_query", f"facet_limit muss zwischen 1 und {MAX_FACET_LIMIT} liegen.")
        if content_type not in CONTENT_TYPES:
            raise EduSharingError("invalid_query", f"Unbekannter Inhaltstyp '{content_type}'.")
        key = (self.instance.id, content_type, norm_criteria, norm_facets, facet_limit)

        async def load() -> Aggregate:
            return await self._search(norm_criteria, norm_facets, facet_limit, content_type)

        value, cached = await self._cache.get_or_load(key, load, refresh=refresh)
        return replace(value, cached=True) if cached else value

    # ---- intern --------------------------------------------------------------

    @staticmethod
    def _normalize_criteria(criteria: Iterable[Criterion]) -> tuple[Criterion, ...]:
        seen: dict[str, Criterion] = {}
        for crit in criteria:
            if not isinstance(crit.property, str) or not crit.property.strip():
                raise EduSharingError("invalid_query", "Ein Filter hat kein Feld.")
            if crit.property in seen:
                # Das Repositorium nähme still nur das letzte Kriterium.
                raise EduSharingError(
                    "invalid_query",
                    f"Das Feld '{crit.property}' kommt in mehreren Filtern vor. "
                    "Mehrere Werte gehören in einen Filter.")
            values = tuple(sorted(set(crit.values)))
            if not values or any(not isinstance(v, str) for v in values):
                raise EduSharingError(
                    "invalid_query", f"Der Filter auf '{crit.property}' hat keine Werte.")
            if "" in values and len(values) > 1:
                raise EduSharingError(
                    "invalid_query",
                    f"Der Filter auf '{crit.property}' enthält einen leeren Wert.")
            seen[crit.property] = Criterion(crit.property, values)
        return tuple(seen[p] for p in sorted(seen))

    @staticmethod
    def _normalize_facets(facets: Sequence[str]) -> tuple[str, ...]:
        for facet in facets:
            if not isinstance(facet, str) or not facet.strip():
                raise EduSharingError("invalid_query", "Eine Facette hat kein Feld.")
        return tuple(sorted(set(facets)))

    async def _search(
        self,
        criteria: tuple[Criterion, ...],
        facets: tuple[str, ...],
        facet_limit: int,
        content_type: str,
    ) -> Aggregate:
        inst = self.instance
        url = (f"{inst.rest_url}/search/v1/queries/{quote(inst.repository, safe='')}/"
               f"{quote(inst.metadataset, safe='')}/{quote(inst.query, safe='')}")
        body = {
            "criteria": [{"property": c.property, "values": list(c.values)} for c in criteria],
            "facets": [{"property": f} for f in facets],
            "facetLimit": facet_limit,
            "facetMinCount": 1,
        }
        # maxItems=0: Es werden nie Datensätze angefordert.
        params = {"contentType": content_type, "maxItems": "0", "skipCount": "0"}
        data = await self._request("POST", url, params=params, json=body)
        return self._parse(data, facets)

    def _parse(self, data: Any, wanted: tuple[str, ...]) -> Aggregate:
        bad = EduSharingError(
            "bad_response", "Die Antwort der Suche hat ein unerwartetes Format.", 200)
        if not isinstance(data, dict):
            raise bad
        pagination = data.get("pagination")
        if not isinstance(pagination, dict) or not _is_int(pagination.get("total")):
            raise bad
        facets: dict[str, Facet] = {}
        for raw in data.get("facets") or []:
            if not isinstance(raw, dict) or not isinstance(raw.get("property"), str):
                raise bad
            values = []
            for item in raw.get("values") or []:
                if (not isinstance(item, dict) or not isinstance(item.get("value"), str)
                        or not _is_int(item.get("count"))):
                    raise bad
                values.append(FacetValue(item["value"], item["count"]))
            other = raw.get("sumOtherDocCount") or 0
            if not _is_int(other):
                raise bad
            facets[raw["property"]] = Facet(raw["property"], tuple(values), other)
        # Nur die angefragten Facetten weitergeben; ``nodes`` und alles Übrige wird verworfen.
        return Aggregate(
            total=pagination["total"],
            facets={p: facets.get(p, Facet(p, ())) for p in wanted},
            fetched_at=self._clock(),
        )

    async def _request(self, method: str, url: str, **kwargs: Any) -> Any:
        last: EduSharingError | None = None
        for attempt in range(self._retries + 1):
            if attempt:
                await self._sleep(self._backoff * 2 ** (attempt - 1))
            try:
                async with self._semaphore:
                    response = await self._http.request(
                        method, url, auth=self._auth, timeout=self._timeout,
                        headers={"Accept": "application/json"}, **kwargs)
            except httpx.TimeoutException:
                last = EduSharingError(
                    "timeout", f"{self.instance.name} hat nicht rechtzeitig geantwortet.")
                continue
            except httpx.HTTPError:
                # Text der Ausnahme bewusst nicht übernehmen (kann URL mit Zugangsdaten enthalten)
                last = EduSharingError(
                    "unavailable", f"{self.instance.name} ist nicht erreichbar.")
                continue
            if response.status_code in _RETRY_STATUS:
                last = self._http_error(response)
                continue
            if response.status_code >= 400:
                raise self._http_error(response)
            try:
                return response.json()
            except ValueError:
                raise EduSharingError(
                    "bad_response",
                    f"{self.instance.name} hat kein JSON geliefert.",
                    response.status_code) from None
        assert last is not None
        raise last

    def _http_error(self, response: httpx.Response) -> EduSharingError:
        status = response.status_code
        name = self.instance.name
        if status >= 500:
            return EduSharingError(
                "unavailable", f"{name} meldet einen Serverfehler ({status}).", status)
        if status in (401, 403):
            return EduSharingError(
                "rejected", f"{name} verweigert den Zugriff ({status}).", status)
        text = ""
        try:
            payload = response.json()
            if isinstance(payload, dict) and isinstance(payload.get("message"), str):
                text = payload["message"]
        except ValueError:
            pass
        # Nur erkannte Muster werden zu einer Meldung; der Rohtext geht nicht weiter.
        if match := re.search(rf"Could not find parameter {_NAME} in the query", text):
            return EduSharingError(
                "unknown_property",
                f"{name} kennt das Filterfeld '{match.group(1)}' nicht.", status)
        if match := re.search(rf"Widget {_NAME} was not found", text):
            return EduSharingError(
                "unknown_facet",
                f"{name} kennt das Facettenfeld '{match.group(1)}' nicht.", status)
        if "multiple values of a non-multivalue field" in text:
            return EduSharingError(
                "single_value",
                f"{name} erlaubt für eines der Filterfelder nur einen Wert.", status)
        return EduSharingError(
            "rejected",
            f"{name} hat die Abfrage abgelehnt ({status}). Mögliche Ursachen: unbekanntes "
            "Filter- oder Facettenfeld oder mehrere Werte für ein einwertiges Feld.", status)
