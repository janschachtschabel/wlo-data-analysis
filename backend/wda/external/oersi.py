"""OERSI (oersi.org): Zählungen nach Lizenz und Fächergruppe über die Suchschnittstelle.

Angefragt werden nur Aggregationen (``size: 0``); Treffer kommen nie zurück.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from typing import Any

import httpx

from ..cache import TTLCache
from ..edusharing.errors import EduSharingError

SUBJECT_PREFIX = "https://w3id.org/kim/hochschulfaechersystematik/"
NO_LICENSE = "__none__"
_CC = re.compile(r"creativecommons\.org/licenses/([a-z-]{2,11})(?:/|$)", re.IGNORECASE)
_NOTATION = re.compile(r"/hochschulfaechersystematik/(n\d{1,4})$")


def license_key(url: str) -> str:
    """Lizenz-URL von OERSI → Lizenzschlüssel wie im Repositorium (``CC_BY_SA`` …).
    Versionen und Länderfassungen fallen zusammen; Unbekanntes wird ``CUSTOM``."""
    if url == NO_LICENSE or not url:
        return NO_LICENSE
    lowered = url.lower()
    if "creativecommons.org/publicdomain/zero" in lowered:
        return "CC_0"
    if "creativecommons.org/publicdomain/mark" in lowered:
        return "PDM"
    if match := _CC.search(lowered):
        parts = match.group(1).split("-")
        if parts[0] == "by" and set(parts[1:]) <= {"sa", "nc", "nd"}:
            order = [p for p in ("nc", "sa", "nd") if p in parts[1:]]
            return "_".join(["CC", "BY", *order]).upper()
    return "CUSTOM"


def notation(uri: str) -> str | None:
    """``…/hochschulfaechersystematik/n42`` → ``n42``; alles andere hat bei OERSI kein
    Gegenstück."""
    match = _NOTATION.search(uri)
    return match.group(1) if match else None


def _by_license(buckets: Any) -> dict[str, int]:
    out: dict[str, int] = {}
    if not isinstance(buckets, list):
        raise ValueError
    for bucket in buckets:
        key, count = bucket["key"], bucket["doc_count"]
        if not isinstance(key, str) or isinstance(count, bool) or not isinstance(count, int):
            raise ValueError
        name = license_key(key)
        out[name] = out.get(name, 0) + count
    return out


class OersiClient:
    def __init__(self, http: httpx.AsyncClient, cache: TTLCache, url: str,
                 timeout: float = 60.0, clock: Callable[[], float] = time.time) -> None:
        self._http, self._cache, self.url = http, cache, url
        self._timeout, self._clock = timeout, clock

    async def counts(self, sections: dict[str, list[str]], refresh: bool = False
                     ) -> dict[str, Any]:
        """``sections``: Name → Notationen der Destatis-Systematik (``n4``, ``n42`` …).
        Je Abschnitt zählt jedes Material einmal."""
        clean = {name: sorted(set(values)) for name, values in sections.items() if values}
        key = ("oersi", self.url, tuple(sorted((k, tuple(v)) for k, v in clean.items())))

        async def load() -> dict[str, Any]:
            return await self._load(clean)

        value, cached = await self._cache.get_or_load(key, load, refresh=refresh)
        return {**value, "cached": cached}

    async def _load(self, sections: dict[str, list[str]]) -> dict[str, Any]:
        licenses = {"terms": {"field": "license.id", "size": 300, "missing": NO_LICENSE}}
        body: dict[str, Any] = {"size": 0, "track_total_hits": True,
                                "aggs": {"license": licenses}}
        if sections:
            body["aggs"]["sections"] = {
                "filters": {"filters": {
                    name: {"terms": {"about.id": [SUBJECT_PREFIX + n for n in values]}}
                    for name, values in sections.items()}},
                "aggs": {"license": licenses}}
            # ohne Fach: eigene Zählung, damit „ohne Wert" nicht leer bleibt
            body["aggs"]["no_subject"] = {
                "filter": {"bool": {"must_not": {"exists": {"field": "about.id"}}}},
                "aggs": {"license": licenses}}
        try:
            response = await self._http.post(
                self.url, json=body, timeout=self._timeout,
                headers={"Accept": "application/json"})
        except httpx.TimeoutException:
            raise EduSharingError("timeout", "OERSI hat nicht rechtzeitig geantwortet.") from None
        except httpx.HTTPError:
            raise EduSharingError("unavailable", "OERSI ist nicht erreichbar.") from None
        if response.status_code == 429:
            raise EduSharingError("throttled", "OERSI drosselt gerade die Abfragen (429).", 429)
        if response.status_code >= 500:
            raise EduSharingError(
                "unavailable", f"OERSI meldet einen Serverfehler ({response.status_code}).",
                response.status_code)
        if response.status_code != 200:
            raise EduSharingError(
                "rejected", f"OERSI hat die Abfrage abgelehnt ({response.status_code}).",
                response.status_code)
        bad = EduSharingError(
            "bad_response", "Die Antwort von OERSI hat ein unerwartetes Format.", 200)
        try:
            data = response.json()
            total = data["hits"]["total"]["value"]
            if isinstance(total, bool) or not isinstance(total, int) or total < 0:
                raise bad
            aggs = data["aggregations"]
            out: dict[str, Any] = {
                "schema_version": 1, "external": "oersi", "url": self.url, "total": total,
                "by_license": _by_license(aggs["license"]["buckets"]), "sections": {},
                "fetched_at": self._clock()}
            if aggs["license"].get("sum_other_doc_count"):
                raise bad  # unvollständige Lizenzliste wäre eine falsche Summe
            for name in sections:
                bucket = aggs["sections"]["buckets"][name]
                count = bucket["doc_count"]
                if isinstance(count, bool) or not isinstance(count, int):
                    raise bad
                if bucket["license"].get("sum_other_doc_count"):
                    raise bad
                out["sections"][name] = {
                    "total": count, "by_license": _by_license(bucket["license"]["buckets"])}
            if sections:
                bucket = aggs["no_subject"]
                count = bucket["doc_count"]
                if (isinstance(count, bool) or not isinstance(count, int)
                        or bucket["license"].get("sum_other_doc_count")):
                    raise bad
                out["missing"] = {
                    "total": count, "by_license": _by_license(bucket["license"]["buckets"])}
        except (ValueError, KeyError, TypeError):
            raise bad from None
        return out


def series_values(counts: dict[str, Any], mapping: dict[str, Any]) -> dict[str, int | None]:
    """Spalten eines Blocks aus den Lizenzzahlen: ``"total"``, ``"licensed"`` oder eine Liste
    von Lizenzschlüsseln je Spalte."""
    by_license = counts.get("by_license") or {}
    out: dict[str, int | None] = {}
    for series_id, rule in mapping.items():
        if rule == "total":
            out[series_id] = counts.get("total")
        elif rule == "licensed":
            out[series_id] = sum(v for k, v in by_license.items() if k != NO_LICENSE)
        else:
            out[series_id] = sum(by_license.get(k, 0) for k in rule)
    return out
