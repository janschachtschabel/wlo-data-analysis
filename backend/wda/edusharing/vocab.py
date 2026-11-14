"""Felder und Vokabulare aus dem Metadatenset, Auflösung zwischen Label und URI."""

from __future__ import annotations

import asyncio
import difflib
import json
import logging
import os
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .client import EduSharingClient
from .errors import EduSharingError

log = logging.getLogger("wda.vocab")
_FORMAT = 1
EMPTY_LABEL = "(leer)"  # Anzeige für den leeren Wert, der in Facetten vorkommt
MAX_SUGGESTIONS = 8
FAILURE_TTL = 30.0
MAX_FUZZY_LABELS = 3000


@dataclass(frozen=True)
class VocabValue:
    id: str
    caption: str
    parent: str | None = None
    plain: str | None = None   # Name ohne den Zusatz des Vokabulars, falls einer dazukam

    @property
    def bare(self) -> str:
        return self.plain if self.plain is not None else self.caption


@dataclass(frozen=True)
class Field:
    property: str
    caption: str
    type: str
    values: tuple[VocabValue, ...] = ()


@dataclass(frozen=True)
class Resolution:
    value: str
    label: str
    how: str  # "uri" | "label" | "literal"


class UnresolvedValue(Exception):
    """Ein Filterwert passt zu keinem oder zu mehreren Einträgen des Vokabulars."""

    def __init__(self, property: str, text: str, suggestions: list[dict[str, Any]],
                 ambiguous: bool = False) -> None:
        reason = "ist mehrdeutig" if ambiguous else "ist im Vokabular nicht bekannt"
        super().__init__(f"Der Wert '{text}' für '{property}' {reason}.")
        self.property = property
        self.text = text
        self.suggestions = suggestions
        self.ambiguous = ambiguous


def _norm(text: str) -> str:
    return " ".join(text.split()).casefold()


def trim_metadataset(raw: dict[str, Any]) -> list[dict[str, Any]]:
    """Kürzt das Metadatenset auf Felder und Werte (``id``, ``caption``, ``parent``).

    Je Widget-ID bleibt ein Eintrag; Varianten mit Werteliste und Beschriftung gewinnen.
    """
    widgets = raw.get("widgets")
    if not isinstance(widgets, list):
        raise EduSharingError("bad_response", "Das Metadatenset enthält keine Felder.", 200)
    fields: dict[str, dict[str, Any]] = {}
    for widget in widgets:
        if not isinstance(widget, dict) or not isinstance(widget.get("id"), str):
            continue
        prop = widget["id"]
        values = []
        seen: set[str] = set()
        for value in widget.get("values") or []:
            if not isinstance(value, dict) or not isinstance(value.get("id"), str):
                continue
            if value["id"] in seen:
                continue
            seen.add(value["id"])
            caption = value.get("caption")
            parent = value.get("parent")
            values.append({
                "id": value["id"],
                "caption": caption if isinstance(caption, str) and caption else value["id"],
                "parent": parent if isinstance(parent, str) and parent else None,
            })
        caption = widget.get("caption")
        entry = {
            "property": prop,
            "caption": caption if isinstance(caption, str) and caption else "",
            "type": widget.get("type") if isinstance(widget.get("type"), str) else "",
            "values": values,
        }
        current = fields.get(prop)
        if current is None:
            fields[prop] = entry
            continue
        if len(values) > len(current["values"]):
            current["values"] = values
            current["type"] = entry["type"] or current["type"]
        if not current["caption"]:
            current["caption"] = entry["caption"]
    if not fields:
        # Ein leeres Metadatenset würde jeden Filterwert still zum wörtlichen Wert machen.
        raise EduSharingError("bad_response", "Das Metadatenset enthält keine Felder.", 200)
    for entry in fields.values():
        entry["caption"] = entry["caption"] or entry["property"]
    return list(fields.values())


class VocabStore:
    def __init__(
        self,
        client: EduSharingClient,
        data_dir: Path,
        ttl: float = 86400.0,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._client = client
        inst = client.instance
        self._path = Path(data_dir) / "cache" / f"mds-{inst.id}.json"
        self._ttl = ttl
        self._clock = clock
        self._fields: dict[str, Field] | None = None
        self._expires = 0.0
        self._by_id: dict[str, dict[str, VocabValue]] = {}
        self._by_label: dict[str, dict[str, list[VocabValue]]] = {}
        self._lock = asyncio.Lock()
        self.stale = False  # True: Netz nicht erreichbar, abgelaufene Kopie in Gebrauch
        self._failure: tuple[float, EduSharingError] | None = None

    # ---- öffentliche Aufrufe -------------------------------------------------

    async def fields(self) -> dict[str, Field]:
        if self._fields is not None and self._expires > self._clock():
            return self._fields
        async with self._lock:
            if self._fields is not None and self._expires > self._clock():
                return self._fields
            if self._failure is not None and self._failure[0] > self._clock():
                # Fehlschlag kurz merken, damit Wartende nicht nacheinander erneut laden
                raise self._failure[1]
            try:
                await self._load()
            except EduSharingError as exc:
                self._failure = (self._clock() + FAILURE_TTL, exc)
                raise
            self._failure = None
            assert self._fields is not None
            return self._fields

    async def field(self, property: str) -> Field | None:
        return (await self.fields()).get(property)

    async def label(self, property: str, value: str) -> str:
        await self.fields()
        known = self._by_id.get(property, {}).get(value)
        if known:
            return known.caption
        return value if value.strip() else EMPTY_LABEL

    async def plain(self, property: str, value: str) -> str:
        """Der Name ohne Zusatz des Vokabulars: „Mathematik“ statt „Mathematik (Schule)“."""
        await self.fields()
        known = self._by_id.get(property, {}).get(value)
        return known.bare if known else await self.label(property, value)

    async def check_facet(self, property: str) -> None:
        """Erlaubt Facetten nur auf Feldern mit Werteliste oder aus der Freigabeliste der
        Instanz. Eine Facette über Titel oder URL wäre eine Liste einzelner Datensätze."""
        field = await self.field(property)
        if (field is not None and field.values) or property in self._client.instance.facet_fields:
            return
        raise EduSharingError(
            "invalid_query",
            f"Das Feld '{property}' ist nicht als Facette freigegeben. Erlaubt sind Felder "
            "mit Werteliste und die für die Instanz freigegebenen Felder.")

    async def resolve(self, property: str, text: str, literal: bool = False) -> Resolution:
        """``literal=True`` übernimmt den Wert unverändert, etwa einen Facettenwert, den das
        Vokabular nicht kennt."""
        if literal:
            return Resolution(text, await self.label(property, text), "literal")
        field = await self.field(property)
        if field is None or not field.values:
            # Feld ohne Werteliste (z. B. Lizenzschlüssel, Quelle): Wert wörtlich nehmen
            return Resolution(text.strip(), text.strip(), "literal")
        by_id = self._by_id[property]
        if text in by_id:
            return Resolution(text, by_id[text].caption, "uri")
        if text.strip() in by_id:
            value = by_id[text.strip()]
            return Resolution(value.id, value.caption, "uri")
        matches = self._by_label[property].get(_norm(text), []) if text.strip() else []
        if len(matches) == 1:
            return Resolution(matches[0].id, matches[0].caption, "label")
        if len(matches) > 1:
            raise UnresolvedValue(property, text, [self._suggest(field, m) for m in matches],
                                  ambiguous=True)
        raise UnresolvedValue(property, text, self._suggestions(field, text))

    # ---- intern --------------------------------------------------------------

    def _suggest(self, field: Field, value: VocabValue) -> dict[str, Any]:
        parent = self._by_id[field.property].get(value.parent) if value.parent else None
        return {"id": value.id, "caption": value.caption,
                "parent_caption": parent.caption if parent else None}

    def _suggestions(self, field: Field, text: str) -> list[dict[str, Any]]:
        needle = _norm(text)
        if not needle:
            return []
        found: list[VocabValue] = []
        contains = [v for v in field.values if needle in _norm(v.caption)]
        contains.sort(key=lambda v: (not _norm(v.caption).startswith(needle), len(v.caption)))
        found.extend(contains)
        labels = self._by_label[field.property]
        if len(found) < MAX_SUGGESTIONS and len(labels) <= MAX_FUZZY_LABELS:
            for close in difflib.get_close_matches(needle, list(labels), n=MAX_SUGGESTIONS,
                                                   cutoff=0.6):
                for value in labels[close]:
                    if value not in found:
                        found.append(value)
        return [self._suggest(field, v) for v in found[:MAX_SUGGESTIONS]]

    def _install(self, entries: list[dict[str, Any]], expires: float) -> None:
        fields: dict[str, Field] = {}
        by_id: dict[str, dict[str, VocabValue]] = {}
        by_label: dict[str, dict[str, list[VocabValue]]] = {}
        for entry in entries:
            plain = tuple(VocabValue(v["id"], v["caption"], v.get("parent"))
                          for v in entry["values"])
            values = self._told_apart(plain)
            prop = entry["property"]
            fields[prop] = Field(prop, entry["caption"], entry["type"], values)
            by_id[prop] = {v.id: v for v in values}
            labels: dict[str, list[VocabValue]] = {}
            for value, old in zip(values, plain, strict=True):
                labels.setdefault(_norm(value.caption), []).append(value)
                if old.caption != value.caption:  # der bisherige Name bleibt suchbar
                    labels.setdefault(_norm(old.caption), []).append(value)
            by_label[prop] = labels
        self._fields, self._by_id, self._by_label = fields, by_id, by_label
        self._expires = expires

    def _told_apart(self, values: tuple[VocabValue, ...]) -> tuple[VocabValue, ...]:
        """Gleichnamige Werte aus verschiedenen Vokabularen eines Feldes bekommen den Namen
        ihres Vokabulars dazu („Mathematik (Schule)“). Reicht das nicht, kommt dazu, worunter
        der Wert steht, sonst das Ende seiner Kennung."""
        names = self._client.instance.vocab_names
        if not names:
            return values

        prefixes = sorted(names, key=len, reverse=True)   # der längste Anfang gewinnt

        def origin(value: VocabValue) -> str | None:
            return next((names[prefix] for prefix in prefixes
                         if value.id.startswith(prefix)), None)

        same: dict[str, list[tuple[VocabValue, str | None]]] = {}
        for value in values:
            same.setdefault(_norm(value.caption), []).append((value, origin(value)))
        by_id = {v.id: v for v in values}
        out: dict[str, str] = {}
        levels = self._client.instance.vocab_levels

        def level(value: VocabValue, name: str) -> str:
            """Die Ebene im Baum mit ihrem Namen („Studienfach“), sonst „Ebene 3“."""
            depth, at = 1, value
            while at.parent and at.parent in by_id and depth < 12:
                depth, at = depth + 1, by_id[at.parent]
            named = next((levels[p] for p in prefixes if p in levels
                          and value.id.startswith(p)), ())
            return named[depth - 1] if depth <= len(named) else f"Ebene {depth}"

        for group in same.values():
            if len(group) < 2:
                continue
            origins = Counter(name for _, name in group)
            wanted: dict[str, str] = {}
            for value, name in group:
                if name is None:
                    continue
                if origins[name] == 1:
                    if len(origins) > 1:  # nur ein Vokabular: der Zusatz unterschiede nichts
                        wanted[value.id] = f"{value.caption} ({name})"
                    continue
                # gleichnamig im selben Vokabular: die Ebene im Baum unterscheidet
                wanted[value.id] = f"{value.caption} ({name}, {level(value, name)})"
            # was danach noch gleich heißt oder einem vorhandenen Namen gleicht, bekommt
            # das Ende seiner Kennung, zuletzt die ganze Kennung
            for attempt in (1, 2):
                seen = Counter(_norm(caption) for caption in wanted.values())
                for value, name in group:
                    caption = wanted.get(value.id)
                    if caption is None or (seen[_norm(caption)] == 1
                                           and _norm(caption) not in same):
                        continue
                    tail = value.id.rstrip("/").rsplit("/", 1)[-1] if attempt == 1 else value.id
                    wanted[value.id] = f"{value.caption} ({name}, {tail})"
            out.update(wanted)
        return tuple(VocabValue(v.id, out[v.id], v.parent, v.caption) if v.id in out else v
                     for v in values)

    def _read_copy(self) -> tuple[list[dict[str, Any]], float] | None:
        inst = self._client.instance
        try:
            stored = json.loads(self._path.read_text(encoding="utf-8"))
            if (stored["format"] != _FORMAT or stored["instance"] != inst.id
                    or stored["base_url"] != inst.base_url
                    or stored["metadataset"] != inst.metadataset
                    or stored["repository"] != inst.repository):
                return None
            entries = stored["fields"]
            for entry in entries:  # Form prüfen, bevor die Kopie verwendet wird
                Field(entry["property"], entry["caption"], entry["type"],
                      tuple(VocabValue(v["id"], v["caption"], v.get("parent"))
                            for v in entry["values"]))
            return entries, float(stored["fetched_at"])
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def _write_copy(self, entries: list[dict[str, Any]], fetched_at: float) -> None:
        inst = self._client.instance
        payload = {"format": _FORMAT, "instance": inst.id, "base_url": inst.base_url,
                   "repository": inst.repository, "metadataset": inst.metadataset,
                   "fetched_at": fetched_at, "fields": entries}
        tmp = self._path.with_name(f"{self._path.name}.{os.getpid()}.{id(self)}.tmp")
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, self._path)
        except OSError as exc:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
            log.warning("Vokabular-Kopie für %s konnte nicht abgelegt werden (%s).",
                        inst.id, exc.__class__.__name__)

    async def _load(self) -> None:
        now = self._clock()
        # Lesen, Kürzen und Schreiben laufen im Thread: das Metadatenset ist bis zu 17 MB groß.
        copy = await asyncio.to_thread(self._read_copy)
        if copy is not None and copy[1] + self._ttl > now:
            self._install(copy[0], copy[1] + self._ttl)
            self.stale = False
            return
        try:
            entries = await asyncio.to_thread(trim_metadataset, await self._client.metadataset())
        except EduSharingError:
            if copy is None:
                raise
            log.warning("Metadatenset von %s nicht ladbar, abgelaufene Kopie wird verwendet.",
                        self._client.instance.id)
            # in fünf Minuten erneut versuchen
            self._install(copy[0], now + min(300.0, self._ttl))
            self.stale = True
            return
        await asyncio.to_thread(self._write_copy, entries, now)
        self._install(entries, now + self._ttl)
        self.stale = False
