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
# Das Repositorium liefert bis zu 5 × facetLimit Werte. 5000 reicht für jedes Vokabular
# und jede Quellenliste, verhindert aber, dass eine Facette zur Datensatzliste wird.
MAX_FACET_LIMIT = 5000
_ABOUT_KEYS = ("repository", "renderservice", "major", "minor")
_RETRY_STATUS = frozenset({429, 502, 503, 504})
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


MAX_VALUE_LENGTH = 2000
MAX_VALUES = 5000


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_count(value: Any) -> bool:
    return _is_int(value) and value >= 0


def _valid_name(name: Any) -> bool:
    return (isinstance(name, str) and 0 < len(name) <= 200
            and not any(ch.isspace() for ch in name))


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
        return {k: version[k] for k in _ABOUT_KEYS
                if isinstance(version.get(k), str | int) and not isinstance(version.get(k), bool)}

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
        # Auch Adresse und Abfrage-Profil: Dieselbe ID kann später woandershin zeigen.
        inst = self.instance
        key = (inst.id, inst.base_url, inst.repository, inst.metadataset, inst.query,
               inst.username, content_type, norm_criteria, norm_facets, facet_limit)

        async def load() -> Aggregate:
            return await self._search(norm_criteria, norm_facets, facet_limit, content_type)

        value, cached = await self._cache.get_or_load(key, load, refresh=refresh)
        return replace(value, cached=True) if cached else value

    # ---- intern --------------------------------------------------------------

    @staticmethod
    def _normalize_criteria(criteria: Iterable[Criterion]) -> tuple[Criterion, ...]:
        seen: dict[str, Criterion] = {}
        for crit in criteria:
            if not _valid_name(crit.property):
                raise EduSharingError(
                    "invalid_query", "Ein Filter hat kein gültiges Feld (leer oder mit Leerraum).")
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
            if any(len(v) > MAX_VALUE_LENGTH for v in values) or len(values) > MAX_VALUES:
                raise EduSharingError(
                    "invalid_query", f"Der Filter auf '{crit.property}' ist zu groß.")
            seen[crit.property] = Criterion(crit.property, values)
        return tuple(seen[p] for p in sorted(seen))

    @staticmethod
    def _normalize_facets(facets: Sequence[str]) -> tuple[str, ...]:
        for facet in facets:
            if not _valid_name(facet):
                raise EduSharingError(
                    "invalid_query",
                    "Eine Facette hat kein gültiges Feld (leer oder mit Leerraum).")
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
        if not isinstance(pagination, dict) or not _is_count(pagination.get("total")):
            raise bad
        ignored = data.get("ignored")
        if ignored:
            # Ein still ignorierter Filter ergäbe eine zu große Zahl; lieber keine als diese.
            names = [x for x in ignored if isinstance(x, str)
                     and re.fullmatch(r"[\w:.\-]{1,100}", x)] \
                if isinstance(ignored, list) else []
            raise EduSharingError(
                "ignored",
                f"{self.instance.name} hat Teile der Abfrage nicht ausgewertet"
                + (f": {', '.join(names[:10])}." if names else "."), 200)
        raw_facets = data.get("facets")
        if raw_facets is None:
            raw_facets = []
        if not isinstance(raw_facets, list):
            raise bad
        facets: dict[str, Facet] = {}
        for raw in raw_facets:
            if not isinstance(raw, dict) or not isinstance(raw.get("property"), str):
                raise bad
            raw_values = raw.get("values")
            if raw_values is None:
                raw_values = []
            if not isinstance(raw_values, list):
                raise bad
            values = []
            for item in raw_values:
                if (not isinstance(item, dict) or not isinstance(item.get("value"), str)
                        or not _is_count(item.get("count"))):
                    raise bad
                values.append(FacetValue(item["value"], item["count"]))
            other = raw.get("sumOtherDocCount")
            if other is None:
                other = 0
            if not _is_count(other):
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
                # Nicht wiederholen: Ein zweiter Versuch würde nur noch einmal so lange warten.
                raise EduSharingError(
                    "timeout",
                    f"{self.instance.name} hat nicht rechtzeitig geantwortet.") from None
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
            if not 200 <= response.status_code < 300:
                # Weiterleitungen werden nicht verfolgt (Zugangsdaten bleiben beim Host).
                raise EduSharingError(
                    "bad_response",
                    f"{self.instance.name} hat unerwartet mit Status "
                    f"{response.status_code} geantwortet.", response.status_code)
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
        if status == 429:
            return EduSharingError(
                "throttled", f"{name} drosselt gerade die Abfragen (429). Später erneut "
                "versuchen.", status)
        if status == 404:
            return EduSharingError(
                "rejected", f"{name} kennt diese Adresse nicht (404). Basis-URL, Repositorium, "
                "Metadatenset und Abfrage der Instanz prüfen.", status)
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
