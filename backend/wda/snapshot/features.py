"""Aus einem Knoten des Repositoriums werden Merkmale; der Knoten selbst wird verworfen.

Gespeichert werden Längen, Anzahlen und Zeitpunkte. Wo Gleichheit gebraucht wird (dieselbe
URL, derselbe Titel), steht ein Schlüssel aus einem HMAC mit einem Geheimnis der
Installation: Daraus lässt sich weder die URL noch der Titel zurückgewinnen."""

from __future__ import annotations

import hashlib
import hmac
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit

SOURCE, PUBLISHER = "ccm:replicationsource", "ccm:oeh_publisher_combined"
# was vom Repositorium angefordert wird; mehr verlässt die Antwort nicht
PROPERTIES = (
    "cclom:title", "cclom:general_description", "cclom:general_keyword", "ccm:wwwurl",
    SOURCE, PUBLISHER, "ccm:taxonid", "ccm:educationalcontext", "ccm:oeh_lrt_aggregated",
    "ccm:commonlicense_key", "cclom:general_language", "ccm:author_freetext",
    "ccm:educationalintendedenduserrole", "cm:created", "cm:modified")
MAX_GROUPS, MAX_GROUP_CHARS = 20, 200


@dataclass(frozen=True)
class Features:
    id: str
    created: int | None      # Epoch-Sekunden
    modified: int | None
    title_len: int
    desc_len: int
    n_keywords: int
    has_author: int
    has_license: int
    n_subjects: int
    n_levels: int
    n_types: int
    n_languages: int
    n_roles: int
    url_key: str | None      # gleiche URL, gleicher Schlüssel
    url_host: str | None
    url_https: int | None
    title_key: str | None    # gleicher Titel (ohne Groß/Klein und Leerraum), gleicher Schlüssel
    title_is_desc: int
    sources: tuple[str, ...]
    publishers: tuple[str, ...]


def _values(props: dict[str, Any], name: str) -> list[str]:
    raw = props.get(name)
    if raw is None:
        return []
    items = raw if isinstance(raw, list) else [raw]
    return [text for item in items if isinstance(item, str | int | float)
            and not isinstance(item, bool) and (text := str(item).strip())]


def _time(node: dict[str, Any], key: str, prop: str) -> int | None:
    """Zeit eines Knotens: ISO-Text im Knotenfeld oder Epoch-Millisekunden in den Feldern."""
    raw = node.get(key)
    if isinstance(raw, str) and raw:
        try:
            return int(datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp())
        except ValueError:
            pass
    for text in _values(node.get("properties") or {}, prop):
        if re.fullmatch(r"\d{9,16}", text):
            number = int(text)
            return number // 1000 if number > 10_000_000_000 else number
    return None


def millis(node: Any, key: str, prop: str) -> int | None:
    """Dieselbe Zeit in Millisekunden, so genau wie das Repositorium sie liefert."""
    if not isinstance(node, dict):
        return None
    props = node.get("properties")
    for text in _values(props if isinstance(props, dict) else {}, prop):
        if re.fullmatch(r"\d{12,14}", text):
            return int(text)
    raw = node.get(key)
    if isinstance(raw, str) and raw:
        try:
            return int(datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp() * 1000)
        except ValueError:
            return None
    return None


def _key(secret: bytes, kind: str, text: str) -> str:
    return hmac.new(secret, f"{kind}\0{text}".encode(), hashlib.sha256).hexdigest()[:24]


def _url(text: str) -> tuple[str, str, int] | None:
    """(Normalform, Rechner, https?) einer Web-Adresse; ``None``, wenn es keine ist."""
    try:
        parts = urlsplit(text.strip())
        host = (parts.hostname or "").lower()
    except ValueError:
        return None
    if parts.scheme.lower() not in ("http", "https") or not host:
        return None
    host = host.removeprefix("www.")
    path = parts.path.rstrip("/")
    # http und https, mit und ohne www, mit und ohne Schrägstrich am Ende: dieselbe Seite
    normal = f"{host}{path}" + (f"?{parts.query}" if parts.query else "")
    return normal, host[:MAX_GROUP_CHARS], int(parts.scheme.lower() == "https")


def _groups(values: list[str]) -> tuple[str, ...]:
    seen: dict[str, None] = {}
    for value in values:
        seen.setdefault(value[:MAX_GROUP_CHARS])
    return tuple(seen)[:MAX_GROUPS]


def features(node: Any, secret: bytes) -> Features | None:
    """``None`` für alles, was kein Knoten mit ID ist."""
    if not isinstance(node, dict):
        return None
    ref = node.get("ref")
    node_id = ref.get("id") if isinstance(ref, dict) else None
    if not isinstance(node_id, str) or not re.fullmatch(r"[\w\-]{1,100}", node_id):
        return None
    props = node.get("properties")
    props = props if isinstance(props, dict) else {}
    title = " ".join(" ".join(_values(props, "cclom:title")).split())
    desc = " ".join(" ".join(_values(props, "cclom:general_description")).split())
    url = next((u for text in _values(props, "ccm:wwwurl") if (u := _url(text))), None)
    return Features(
        id=node_id,
        created=_time(node, "createdAt", "cm:created"),
        modified=_time(node, "modifiedAt", "cm:modified"),
        title_len=len(title), desc_len=len(desc),
        n_keywords=len(_values(props, "cclom:general_keyword")),
        has_author=int(bool(_values(props, "ccm:author_freetext"))),
        has_license=int(bool(_values(props, "ccm:commonlicense_key"))),
        n_subjects=len(_values(props, "ccm:taxonid")),
        n_levels=len(_values(props, "ccm:educationalcontext")),
        n_types=len(_values(props, "ccm:oeh_lrt_aggregated")),
        n_languages=len(_values(props, "cclom:general_language")),
        n_roles=len(_values(props, "ccm:educationalintendedenduserrole")),
        url_key=_key(secret, "url", url[0]) if url else None,
        url_host=url[1] if url else None,
        url_https=url[2] if url else None,
        title_key=_key(secret, "title", title.casefold()) if title else None,
        title_is_desc=int(bool(title) and title.casefold() == desc.casefold()),
        sources=_groups(_values(props, SOURCE)),
        publishers=_groups(_values(props, PUBLISHER)),
    )
