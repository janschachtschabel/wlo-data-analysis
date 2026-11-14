"""Themen je Zelle („Wissenslandkarte“): je Zeile und Spalte die häufigsten oder typischen
Werte eines Feldes, etwa Schlagwörter je Fach und Bildungsstufe.

Je Zelle eine Abfrage mit der Facette des Themenfeldes. Freie Felder (ohne Werteliste)
werden bereinigt: Schreibweisen zusammengeführt, Füllwörter, die Namen von Zeile und Spalte
und die Werte ausgeschlossener Felder (Anbieter, Quellen) weggelassen. Es bleiben Zählungen;
ein Wert, den nur ein Material trägt, erscheint nie."""

from __future__ import annotations

import asyncio
import math
import re
from typing import Any

from ..edusharing.client import Aggregate, Criterion, EduSharingClient
from ..edusharing.errors import EduSharingError
from ..edusharing.vocab import VocabStore
from ..errors import Notes, QueryError
from ..filters import resolve_filters, resolve_values
from .engine import (
    _Column,
    _helper,
    _plan_columns,
    _refused,
    _result,
    _Row,
    _Run,
    _select_rows,
    _sort_rows,
)
from .spec import MAX_TOPIC_CELLS, QuerySpec

CELL_FACET_LIMIT = 40      # das Repositorium liefert das Fünffache: 200 Werte je Zelle
BASE_FACET_LIMIT = 2000    # Vergleichsbasis: die 10.000 häufigsten Werte des Bestands
MIN_COUNT = 2              # ein Wert, den nur ein Material trägt, ist kein Thema
MIN_LIFT = 1.2
BLOCK_SIZE = 4             # so viele Werte mit fast gleicher Zahl sind der Satz eines Kanals
BLOCK_SPREAD = 0.03
BLOCK_MIN = 20
_YEAR = re.compile(r"(19|20)\d\d")


def _blocks(items: list[tuple[str, str, int]]) -> set[str]:
    """Schlüssel von Werten, die in einer Zelle zu viert oder mehr fast dieselbe Zahl tragen:
    Ein Kanal hängt denselben Satz Schlagwörter an alle seine Materialien. Themen, die der
    Bestand wirklich trägt, verteilen sich nie so gleichmäßig."""
    ranked = sorted((item for item in items if item[2] >= BLOCK_MIN), key=lambda i: -i[2])
    out: set[str] = set()
    start = 0
    while start < len(ranked):
        end = start + 1
        while end < len(ranked) and ranked[end][2] >= ranked[start][2] * (1 - BLOCK_SPREAD):
            end += 1
        if end - start >= BLOCK_SIZE:
            out |= {key for key, _, _ in ranked[start:end]}
        start = end
    return out

# Wörter aus Video-Schlagwörtern, die nichts über das Thema sagen
FILLER = frozenset("""
einfach erklärt erklaert erklärung erklären erklärvideo erklärfilm lernen lernvideo lernvideos
lernfilm nachhilfe schule schüler schülerinnen unterricht unterrichtsmaterial lehrer lehrerin
bildung wissen video videos film tutorial tutorials online kostenlos gratis free kurs kurse
übung übungen uebung aufgabe aufgaben beispiel beispiele lösung lösungen hilfe tipps tipp
lerntipps abitur abi prüfung klausur klassenarbeit hausaufgaben studium uni universität
student schnell leicht verstehen verständlich grundlagen einführung zusammenfassung überblick
wiederholung education learning school teaching lesson howto explained science oer english
deutsch german youtube podcast audio material materialien arbeitsblatt arbeitsblätter mint
klasse gymnasium realschule hauptschule grundschule unterstufe mittelstufe oberstufe
bildungsmaterial freude spaß spass simpel gut schlecht kontra contra original footage
documentary learn speak read berechnen rechnen lernhilfe mathehilfe nachhilfelehrer ausbildung
worksheet template workshop seminar medienpaket multimediales general main lehre digitale
mensch jahr name stadt meter million wort einwohner music förderschule lernmaterial
""".split()) | {
    "mathe nachhilfe", "video tutorial", "online class", "learn english", "mp4-version",
    "webm-version", "akademische grundlagen", "selbstgesteuertes lernen",
    "außerschulische bildung", "kids & family", "religion & spirituality", "places & travel",
    "self-improvement", "tv & film", "society & culture",
    "einfach erklärt", "schnell erklärt", "leicht erklärt", "how to", "online learning",
    "open educational resources", "lehr- und lernmaterial"}

_SPACE = re.compile(r"[\s_]+")


def norm(value: str) -> str:
    """Eine Schreibweise je Thema: ohne Groß/Klein, Raute, doppelte Leerzeichen."""
    return _SPACE.sub(" ", value.strip().lstrip("#").casefold()).strip(" .,;:!?\"'„“")


def _merge(values: list[tuple[str, int]]) -> dict[str, tuple[str, int]]:
    """Schreibweisen zusammenführen: Schlüssel → (häufigste Schreibweise, Summe)."""
    out: dict[str, tuple[str, int, int]] = {}
    for value, count in values:
        key = norm(value)
        if not key:
            continue
        shown = value.strip().lstrip("#").strip()
        label, total, best = out.get(key, (shown, 0, 0))
        if count > best:
            label, best = shown, count
        out[key] = (label, total + count, best)
    return {key: (label, total) for key, (label, total, _) in out.items()}


def _names(label: str) -> set[str]:
    """Der Name einer Zeile oder Spalte und seine Wörter: „Mathematik“ ist in der Zelle
    Mathematik kein Thema."""
    whole = norm(label)
    return {whole} | {w for w in re.split(r"[\s/,()&-]+", whole) if len(w) >= 4}


def _is_name(key: str, names: set[str]) -> bool:
    if key in names:
        return True
    # Kurzformen wie „Mathe“ für „Mathematik“
    return len(key) >= 4 and any(name.startswith(key) or key.startswith(name)
                                 for name in names if len(name) >= 4)


async def execute_topics(spec: QuerySpec, client: EduSharingClient, vocab: VocabStore,
                         run: _Run) -> dict[str, Any]:
    topics, rows_spec = spec.topics, spec.rows
    assert topics is not None and rows_spec is not None
    notes = Notes()
    prop, row_prop = topics.property, rows_spec.property
    col_prop = spec.columns.property if spec.columns else None
    base_list, base_described = await resolve_filters(vocab, spec.filters)
    base = tuple(base_list)
    base_props = {c.property for c in base}
    if prop in (row_prop, col_prop):
        raise QueryError("Das Themenfeld braucht ein anderes Feld als Zeilen und Spalten.")
    for name in (prop, row_prop):
        if name in base_props:
            raise QueryError(
                f"'{name}' steht auch in den Filtern; die Facette ignoriert den Filter auf "
                "dem eigenen Feld. Bitte eines von beiden weglassen.")
        if _helper(name):
            raise QueryError(f"'{name}' ist eine Suchhilfe des Repositoriums, kein "
                             "gespeichertes Feld.")
        await vocab.check_facet(name)
    for name in topics.exclude_fields:
        await vocab.check_facet(name)

    # ---- Zeilen und Spalten ----------------------------------------------------------
    include = await resolve_values(vocab, row_prop, rows_spec.include) \
        if rows_spec.include else None
    main = await run.call(base, row_prop)
    row_facet = main.facets[row_prop]
    counts = {v.value: v.count for v in row_facet.values}
    keys = [i["value"] for i in include] if include else [k for k in counts if k.strip()]
    rows = [_Row(k, await vocab.label(row_prop, k), "value") for k in keys]
    if rows_spec.match:
        rows = await _select_rows(rows, rows_spec, vocab, notes)
    count_column = _Column("count", "Anzahl")
    for row in rows:
        count_column.cells[row.key] = counts.get(row.key, None if row_facet.truncated else 0)
    rows = _sort_rows(rows, [count_column], rows_spec, include, False)
    if rows_spec.top is not None and len(rows) > rows_spec.top:
        notes.add("rows_top",
                  f"Angezeigt werden die ersten {rows_spec.top} von {len(rows)} Zeilen.")
        rows = rows[: rows_spec.top]
    if spec.columns:
        columns = await _plan_columns(spec, vocab, run, base, base_props, row_prop, notes)
    else:
        columns = [_Column("count", "Anzahl")]
    if len(rows) * len(columns) > MAX_TOPIC_CELLS:
        raise QueryError(f"Das wären {len(rows) * len(columns)} Zellen; höchstens "
                         f"{MAX_TOPIC_CELLS}. Bitte Zeilen oder Spalten mit top eingrenzen.")

    # ---- freie Felder: bereinigen; Felder mit Werteliste: Bezeichnungen ------------------
    field = await vocab.field(prop)
    free = not (field is not None and field.values)
    blocked: set[str] = set()
    if free:
        if topics.stopwords:
            blocked |= FILLER
        blocked |= {norm(word) for word in topics.exclude}
        for name in topics.exclude_fields:
            try:
                listed = (await run.call(base, name)).facets[name]
            except EduSharingError as exc:
                if not _refused(exc):
                    raise
                continue
            for value in listed.values:
                blocked.add(norm(value.value))
                blocked.add(norm(await vocab.label(name, value.value)))
        blocked.discard("")
    typical = topics.rank == "typical"
    baseline: dict[str, tuple[str, int]] = {}
    stock = 0
    floor = 1
    if typical:
        whole = await run.call(base, prop, BASE_FACET_LIMIT)
        stock = whole.total
        listed = [(v.value, v.count) for v in whole.facets[prop].values]
        baseline = _merge(listed) if free else {v: (v, c) for v, c in listed}
        floor = min((c for _, c in baseline.values()), default=1)

    refused = False

    async def cell(row: _Row, column: _Column) -> Aggregate | None:
        nonlocal refused
        try:
            return await run.call(base + (Criterion(row_prop, (row.key,)),) + column.criteria,
                                  prop, CELL_FACET_LIMIT)
        except EduSharingError as exc:
            if not _refused(exc):
                raise
            refused = True
            return None

    answers = await asyncio.gather(*[cell(row, column) for row in rows for column in columns])
    column_names = [_names(c.label) if spec.columns else set() for c in columns]
    # andere Fächer und Stufen als Schlagwort sind ebenfalls keine Themen
    blocked |= {norm(row.label) for row in rows}
    if spec.columns:
        blocked |= {norm(c.label) for c in columns}
    out_rows = []
    merged_any = False
    blocks_any = False
    known = {v.id for v in field.values} if field is not None and not free else set()
    labels = {v.id: v.caption for v in field.values} if field is not None and not free else {}
    for r, row in enumerate(rows):
        row_names = _names(row.label)
        values: list[int | None] = []
        cells: list[list[dict[str, Any]]] = []
        for c, _column in enumerate(columns):
            answer = answers[r * len(columns) + c]
            if answer is None:
                values.append(None)
                cells.append([])
                continue
            values.append(answer.total)
            found = [(v.value, v.count) for v in answer.facets[prop].values if v.value.strip()]
            if free:
                merged = _merge(found)
                merged_any = merged_any or len(merged) < len(found)
                names = row_names | column_names[c]
                items = [(key, label, count) for key, (label, count) in merged.items()
                         if key not in blocked and not _is_name(key, names)
                         and len(key) > 2 and not _YEAR.fullmatch(key)]
                boilerplate = _blocks(items) if spec.topics.stopwords else set()
                if boilerplate:
                    blocks_any = True
                    items = [item for item in items if item[0] not in boilerplate]
            else:
                # nur Werte, die das Vokabular kennt, und nicht den Namen der Zeile selbst
                items = [(value, value, count) for value, count in found
                         if value in known and norm(labels[value]) not in row_names
                         and norm(labels[value]) != norm(row.label)]
            ranked = []
            for key, label, count in items:
                if count < MIN_COUNT:
                    continue
                score = float(count)
                lift = None
                if typical and stock and answer.total:
                    everywhere = baseline.get(key, (label, floor))[1]
                    lift = (count / answer.total) / (max(everywhere, count) / stock)
                    if lift < MIN_LIFT:
                        continue
                    score = count * math.log(lift)
                ranked.append((score, count, key, label, lift))
            ranked.sort(key=lambda item: (-item[0], -item[1], item[2]))
            # ausgewählt nach dem, was typisch ist; gezeigt nach der Zahl der Materialien
            chosen = sorted(ranked[: topics.top], key=lambda item: (-item[1], item[2]))
            cells.append([
                {"label": label if free else labels[key], "count": count,
                 "share": round(100 * count / answer.total, 1) if answer.total else None,
                 **({"lift": round(lift, 1)} if lift is not None else {})}
                for _, count, key, label, lift in chosen])
        out_rows.append({"key": row.key, "label": row.label, "kind": "value",
                         "values": values, "topics": cells})

    for column in columns:
        column.total = (await run.call(base + column.criteria)).total
    if free:
        notes.add("topics_cleaned",
                  "Schlagwörter sind bereinigt: Schreibweisen (Groß/Klein, Raute) sind "
                  "zusammengeführt, der Name von Zeile und Spalte"
                  + (", Füllwörter" if topics.stopwords else "")
                  + (", Namen von Anbietern und Quellen" if topics.exclude_fields else "")
                  + " fehlen"
                  + (", ebenso Sätze von Schlagwörtern mit fast gleicher Zahl (ein Kanal "
                     "hängt sie an alle seine Materialien)" if blocks_any else "")
                  + ". Trägt ein Material zwei Schreibweisen, zählt es doppelt.")
    notes.add("topics",
              ("Gezeigt ist, was in der Zelle deutlich häufiger vorkommt als im Bestand "
               "(ausgewählt nach Anzahl mal Logarithmus des Verhältnisses, gereiht nach "
               "Anzahl). "
               if typical else "Gezeigt sind die häufigsten Werte je Zelle. ")
              + f"Je Zelle höchstens {topics.top}, nur Werte mit mindestens {MIN_COUNT} "
              f"Materialien, ausgewertet unter den {CELL_FACET_LIMIT * 5} häufigsten der "
              "Zelle. Die Zahl oben in der Zelle ist die Anzahl der Materialien.")
    if refused:
        notes.add("topics_refused", "Einzelne Zellen sind nicht bestimmbar: Das Repositorium "
                                    "zählt die Kombination nicht.")
    result = _result(
        spec, client, run, notes,
        [{"id": c.id, "label": c.label, "derived": False, "total": c.total} for c in columns],
        out_rows, base_described,
        {"property": row_prop, "mode": "facet",
         "include": [i["value"] for i in include] if include else None, "groups": []})
    result["topics"] = {"property": prop, "rank": topics.rank, "free": free}
    return result
