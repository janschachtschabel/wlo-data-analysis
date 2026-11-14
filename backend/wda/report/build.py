"""Baut aus einem gespeicherten Lauf den Bericht."""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from typing import Any

from ..engine.engine import SCHEMA_VERSION
from ..engine.formula import compile_formula
from ..errors import QueryError
from ..external.oersi import EXTERNAL_SCHEMA_VERSION, series_values
from .model import Block, Column, Failure, Report, Row, Table, Text

log = logging.getLogger(__name__)
_PLACEHOLDER = re.compile(r"\{\{\s*([^{}\n]{1,300}?)\s*\}\}")


def format_number(value: Any, digits: int | None = None) -> str:
    """Deutsches Zahlenformat; fehlende Werte als Gedankenstrich, nie als 0."""
    if value is None or isinstance(value, bool) or not isinstance(value, int | float):
        return "–"
    if isinstance(value, float) and value.is_integer() and digits is None:
        value = int(value)
    if isinstance(value, int):
        text = f"{value:,}"
    else:
        text = f"{value:,.{2 if digits is None else digits}f}"
    return text.replace(",", " ").replace(".", ",").replace(" ", ".")


def format_time(value: Any) -> str:
    """Zeitpunkt als ``TT.MM.JJJJ HH:MM`` (UTC); akzeptiert Epoch-Sekunden und ISO-Text."""
    try:
        if isinstance(value, int | float) and not isinstance(value, bool):
            moment = datetime.fromtimestamp(value, UTC)
        else:
            moment = datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(UTC)
    except (ValueError, OverflowError, OSError):
        return "–"
    return moment.strftime("%d.%m.%Y %H:%M") + " UTC"


def describe_filters(filters: list[dict[str, Any]]) -> str:
    parts = []
    for flt in filters or []:
        labels = [str(v.get("label") or v.get("value")) for v in flt.get("values", [])]
        shown = ", ".join(labels[:6]) + (" …" if len(labels) > 6 else "")
        parts.append(f"{flt.get('property')} = {shown}")
    return "; ".join(parts)


def table_from_result(key: str, title: str, result: dict[str, Any],
                      view: dict[str, Any] | None = None) -> Table | Failure:
    if result.get("schema_version") != SCHEMA_VERSION:
        return Failure(key, title,
                       f"Dieses Ergebnis hat das Format {result.get('schema_version')!r}; "
                       f"gelesen wird Format {SCHEMA_VERSION}. Den Lauf bitte neu ausführen.",
                       "unsupported_result")
    columns = [Column(c["id"], c["label"], bool(c.get("derived"))) for c in result["columns"]]
    rows = [Row(r["key"], r["label"], r["kind"], list(r["values"]),
                percent=r.get("percent"), topics=r.get("topics")) for r in result["rows"]]
    # Gesamtzeile: die Zählung über alle Materialien, keine Summe der Zeilen
    if result.get("total_row", True):
        rows.append(
            Row("__total__", "Gesamt", "total", [c.get("total") for c in result["columns"]]))
    prov = result.get("provenance", {})
    inst = prov.get("instance") or {}
    view = view or {"type": "table", "options": {}}
    notes = list(prov.get("notes") or [])
    fill = result.get("fill") if isinstance(result.get("fill"), dict) else None
    asked = (prov.get("spec") or {}).get("fill") if isinstance(prov.get("spec"), dict) else None
    if fill is None and isinstance(asked, dict):
        # Läufe von vor dem Kennzeichen: aus der gespeicherten Abfrage ableiten
        fill = {"show": asked.get("show") or "rate",
                "by_rows": bool((prov.get("spec") or {}).get("rows"))}
    if fill is not None and view.get("type") not in _FILL_VIEWS[bool(fill.get("by_rows"))]:
        # Zahl, Kreis oder Linie ergeben aus Füllgraden kein richtiges Bild
        view = {**view, "type": "table"}
        notes.append({"code": "view_changed",
                      "text": "Die gewählte Darstellung passt nicht zu Füllgraden; gezeigt "
                              "wird die Tabelle."})
    return _checked(Table(
        key, title, columns, rows, view=view, notes=notes, fill=fill,
        sources=[{
            "label": inst.get("id") or result.get("instance"), "kind": "query",
            "instance": inst.get("id") or result.get("instance"),
            "base_url": inst.get("base_url"), "fetched_at": prov.get("fetched_at"),
            "oldest_fetched_at": prov.get("oldest_fetched_at"),
            "queries": prov.get("queries"),
            "filters": describe_filters(prov.get("filters") or []),
        }]))


# Darstellungen für Füllgrade: je Feld (ohne Zeilenfeld) bzw. je Zeile
_FILL_VIEWS = {False: {"table", "heatmap", "bar", "stacked_bar"}, True: {"table", "heatmap", "bar"}}


def _checked(table: Table) -> Table:
    """Ein Lauf aus der Datenbank kann beschädigt sein: lieber ein Fehlerblock als eine
    Tabelle, an der ein Ausgabeformat scheitert."""
    for row in table.rows:
        if len(row.values) != len(table.columns) or not all(
                v is None or (isinstance(v, int | float) and not isinstance(v, bool)
                              and v == v and abs(v) < 1e15) for v in row.values):
            raise ValueError("Zeile passt nicht zu den Spalten")
        if not isinstance(row.label, str) or not isinstance(row.key, str):
            raise ValueError("Zeile ohne Namen")
    if not isinstance(table.view, dict):
        raise ValueError("Ansicht")
    return table


def _lookup(tables: dict[str, Table], run: dict[str, Any], path: str) -> str | None:
    if path == "run.date":
        return format_time(run["started_at"])
    if path == "run.name":
        return str(run["name"])
    if path == "run.instance":
        labels = sorted({str(s["instance"]) for t in tables.values() for s in t.sources
                         if s.get("kind") == "query" and s.get("instance")})
        return ", ".join(labels) if labels else None
    # <abschnitt>.total.<spalte>  oder  <abschnitt>.cell.<zeile>.<spalte>
    for key, table in tables.items():
        if not path.startswith(key + "."):
            continue
        rest = path[len(key) + 1:]
        columns = {c.id: pos for pos, c in enumerate(table.columns)}
        if rest.startswith("total."):
            col = columns.get(rest[6:])
            total = next((r for r in table.rows if r.kind == "total"), None)
            if col is not None and total is not None:
                return format_number(total.values[col])
        elif rest.startswith("cell."):
            rest = rest[5:]
            for row in table.rows:
                if rest.startswith(row.key + ".") and rest[len(row.key) + 1:] in columns:
                    return format_number(row.values[columns[rest[len(row.key) + 1:]]])
    return None


def fill_placeholders(markdown: str, tables: dict[str, Table], run: dict[str, Any],
                      notes: list[dict[str, str]]) -> str:
    """Ersetzt ``{{…}}`` durch Zahlen des Laufs. Es wird nur nachgeschlagen, nie gerechnet;
    Unbekanntes bleibt sichtbar stehen."""
    def replace(match: re.Match[str]) -> str:
        found = _lookup(tables, run, match.group(1))
        if found is None:
            notes.append({"code": "placeholder_unknown",
                          "text": f"Der Platzhalter {{{{{match.group(1)}}}}} ließ sich "
                                  "nicht auflösen."})
            return match.group(0)
        return found

    return _PLACEHOLDER.sub(replace, markdown)


def _block_for(item: dict[str, Any]) -> Table | Failure:
    if item.get("error") is not None or item.get("result") is None:
        error = item.get("error") or {}
        return Failure(item["key"], item["title"],
                       str(error.get("detail") or "Kein Ergebnis."),
                       str(error.get("kind") or "missing"))
    return table_from_result(item["key"], item["title"], item["result"], item.get("view"))


def _source(label: str, result: dict[str, Any]) -> dict[str, Any]:
    prov = result.get("provenance", {})
    inst = prov.get("instance") or {}
    return {"label": label, "kind": "query", "instance": inst.get("id"),
            "base_url": inst.get("base_url"), "fetched_at": prov.get("fetched_at"),
            "oldest_fetched_at": prov.get("oldest_fetched_at"), "queries": prov.get("queries"),
            "filters": describe_filters(prov.get("filters") or [])}


def _external_line(series: list[dict[str, Any]], counts: dict[str, Any],
                   mapping: dict[str, Any]) -> list[Any]:
    """Spalten eines externen Blocks: gezählte aus den Lizenzzahlen, berechnete über
    dieselben Formeln wie bei Abfragen."""
    values: dict[str, Any] = dict.fromkeys((s["id"] for s in series), None)
    values.update(series_values(counts, {k: v for k, v in mapping.items() if k in values}))
    formulas = {s["id"]: compile_formula(s["formula"]) for s in series if s.get("formula")}
    for _ in formulas:  # Formeln dürfen aufeinander verweisen
        for sid, formula in formulas.items():
            values[sid] = formula.evaluate(values)
    return [values[s["id"]] for s in series]


def build_matrix(section: dict[str, Any], results: dict[str, dict[str, Any]]
                 ) -> Table | Failure:
    """Setzt die Abfragen der Blöcke und die Handeinträge zu einer gegliederten Tabelle
    zusammen: je Zeilengruppe die Zwischensumme und darunter ihre Einzelzeilen."""
    sid, series = section["id"], section["series"]
    sections = section["rows"]["sections"]
    width = len(series)
    columns: list[Column] = []
    header_groups: list[tuple[str, int]] = []
    notes: list[dict[str, str]] = []
    sources: list[dict[str, Any]] = []
    # je Block: Zelle(zeilen_key) -> Werte der Spalten; Sonderschlüssel für Gruppe/Gesamt
    per_block: list[dict[str, list[Any]]] = []
    child_keys: dict[str, list[tuple[str, str]]] = {s["id"]: [] for s in sections}
    failed = 0
    alias: dict[str, str] = {}  # Wert, wie er in der Definition steht -> Zeilenschlüssel
    formulas = {s["id"]: compile_formula(s["formula"]) for s in series if s.get("formula")}

    def usable(item: dict[str, Any] | None, label: str,
               version: int = SCHEMA_VERSION) -> dict[str, Any] | None:
        if item is None:
            return None
        if item.get("error") is not None or item.get("result") is None:
            detail = (item.get("error") or {}).get("detail") or "Kein Ergebnis."
            note = {"code": "block_failed", "text": f"{label}: {detail}"}
            if note not in notes:
                notes.append(note)
            return None
        if item["result"].get("schema_version") != version:
            notes.append({"code": "unsupported_result",
                          "text": f"{label}: Ergebnis in einem anderen Format; den Lauf "
                                  "bitte neu ausführen."})
            return None
        return item["result"]

    for block in section["blocks"]:
        header_groups.append((block["label"], width))
        columns += [Column(f"{block['id']}.{s['id']}", s["label"], s.get("formula") is not None)
                    for s in series]
        cells: dict[str, list[Any]] = {}
        per_block.append(cells)
        if block["source"] == "manual":
            def pick(values: dict[str, Any] | None) -> list[Any]:
                given = {s["id"]: (values or {}).get(s["id"]) for s in series}
                for _ in formulas:  # berechnete Spalten, wo nichts eingetragen ist
                    for fid, formula in formulas.items():
                        if (values or {}).get(fid) is None:
                            given[fid] = formula.evaluate(given)
                return [given[s["id"]] for s in series]
            cells["__total__"] = pick(block.get("totals"))
            for key, values in (block.get("sections") or {}).items():
                cells["group:" + key] = pick(values)
            for key, values in (block.get("cells") or {}).items():
                cells["manual:" + key] = pick(values)
            sources.append({"label": block["label"], "kind": "manual",
                            "as_of": block.get("as_of"), "note": block.get("note")})
            continue
        if block["source"] == "oersi":
            counts = usable(results.get(f"{sid}/{block['id']}/oersi"), block["label"],
                            EXTERNAL_SCHEMA_VERSION)
            if counts is None:
                failed += 1
                sources.append({"label": block["label"], "kind": "failed"})
                continue
            cells["__total__"] = _external_line(series, counts, block["counts"])
            for key, part in (counts.get("sections") or {}).items():
                cells["group:" + key] = _external_line(series, part, block["counts"])
            if counts.get("missing"):
                cells["__missing__"] = _external_line(series, counts["missing"],
                                                      block["counts"])
            sources.append({"label": block["label"], "kind": "external", "service": "OERSI",
                            "fetched_at": counts.get("fetched_at"), "queries": 1})
            continue
        detail = usable(results.get(f"{sid}/{block['id']}/rows"), block["label"])
        groups = usable(results.get(f"{sid}/{block['id']}/groups"), block["label"])
        if detail is None and groups is None:
            failed += 1
            sources.append({"label": block["label"], "kind": "failed"})
            continue
        source = _source(block["label"], detail or groups)  # type: ignore[arg-type]
        counted = [r["provenance"].get("queries") for r in (detail, groups)
                   if r is not None and r.get("provenance")]
        source["queries"] = sum(q for q in counted if isinstance(q, int)) or None
        sources.append(source)
        for result in (detail, groups):
            for note in (result or {}).get("provenance", {}).get("notes", []):
                code = note.get("code")
                # Zeilen überschneiden sich in der Matrix immer; die Tabelle sagt es einmal.
                # Bei den Einzelzeilen ist "nicht aufgeführt" die Regel (Zusatzwerte).
                if code == "rows_overlap" or (code == "values_ungrouped"
                                              and result is not groups):
                    continue
                if code == "values_ungrouped":  # je Block andere Werte: Block nennen
                    note = {"code": code, "text": f"{block['label']}: {note['text']}"}
                if note not in notes:
                    notes.append(note)
        if detail is not None:
            cells["__total__"] = [c.get("total") for c in detail["columns"]]
            for row in detail["rows"]:
                cells[("__missing__" if row["kind"] == "missing" else "row:" + row["key"])
                      ] = list(row["values"])
            # welche Eingabe wurde zu welcher Zeile: Reihenfolge wie in der Definition
            resolved = iter((detail.get("provenance", {}).get("rows") or {}).get("include")
                            or [])
            fallback = section["rows"].get("labels") or {}
            labels = {row["key"]: (fallback.get(row["key"], row["label"])
                                   if row["label"] == row["key"] else row["label"])
                      for row in detail["rows"]}
            for sec in sections:
                for written in sec.get("values", []):
                    key = next(resolved, None)
                    if key is not None:
                        alias[written if isinstance(written, str)
                              else str(written.get("value"))] = key
                    if key is not None and (key, labels.get(key, key)) not in child_keys[
                            sec["id"]]:
                        child_keys[sec["id"]].append((key, labels.get(key, key)))
        if groups is not None:
            cells.setdefault("__total__", [c.get("total") for c in groups["columns"]])
            for row in groups["rows"]:
                cells["group:" + row["key"]] = list(row["values"])

    if failed == len(section["blocks"]):
        return Failure(sid, section["title"], "; ".join(n["text"] for n in notes)
                       or "Kein Block lieferte ein Ergebnis.", "block_failed")
    def line(key: str) -> list[Any]:
        return [v for cells in per_block for v in cells.get(key, [None] * width)]

    if not any(child_keys.values()):
        # kein Abfrage-Block hat die Zeilen aufgelöst: Werte der Definition wörtlich nehmen
        fallback = section["rows"].get("labels") or {}
        for sec in sections:
            for value in sec.get("values", []):
                key = value if isinstance(value, str) else str(value.get("value"))
                if (key, fallback.get(key, key)) not in child_keys[sec["id"]]:
                    child_keys[sec["id"]].append((key, fallback.get(key, key)))
    known = {key for keys in child_keys.values() for key, _ in keys}
    resolved_rows = bool(alias)
    for block, cells in zip(section["blocks"], per_block, strict=True):
        if block["source"] != "manual":
            continue
        # Der Schlüssel darf der Wert der Zeile sein oder so lauten wie in der Definition.
        for key in list(block.get("cells") or {}):
            target = key if key in known else alias.get(key)
            if target in known:
                cells.setdefault("row:" + target, cells["manual:" + key])
        lost = sorted(k for k in (block.get("cells") or {})
                      if k not in known and alias.get(k) not in known)
        lost += sorted(f"Gruppe {k}" for k in (block.get("sections") or {})
                       if k not in child_keys)
        if lost:
            shown = ", ".join(repr(k) for k in lost[:5]) + (" …" if len(lost) > 5 else "")
            notes.append({"code": "manual_unmatched",
                          "text": f"{block['label']}: {len(lost)} Handeinträge gehören zu "
                                  f"keiner Zeile der Tabelle und fehlen ({shown}). "
                                  + ("Der Schlüssel muss der Wert der Zeile sein oder so "
                                     "lauten wie in der Definition der Zeilen."
                                     if resolved_rows else
                                     "Die Zeilen ließen sich nicht auflösen, weil kein "
                                     "Abfrage-Block ein Ergebnis hat; es gelten die Werte, "
                                     "wie sie in der Definition stehen.")})

    rows: list[Row] = []
    for sec in sections:
        rows.append(Row(sec["id"], sec["label"], "group", line("group:" + sec["id"]), level=0))
        for key, label in child_keys[sec["id"]]:
            rows.append(Row(key, label, "value", line("row:" + key), level=1))
    if section["rows"].get("missing", True):
        rows.append(Row("__missing__", "ohne Wert", "missing", line("__missing__")))
    rows.append(Row("__total__", "Gesamt", "total", line("__total__")))
    show = section.get("show")
    if show:  # nur die gewünschten Spalten, je Block in der gewünschten Reihenfolge
        ids = [s["id"] for s in series]
        keep = [block_pos * width + ids.index(sid)
                for block_pos in range(len(section["blocks"])) for sid in show]
        columns = [columns[pos] for pos in keep]
        header_groups = [(label, len(show)) for label, _ in header_groups]
        for row in rows:
            row.values = [row.values[pos] for pos in keep]
    notes.append({"code": "matrix_counting",
                  "text": "Ein Material mit mehreren Werten zählt in jeder zutreffenden Zeile. "
                          "Zwischensummen und die Gesamtzeile sind eigene Zählungen (jedes "
                          "Material einmal), keine Summen der Zeilen."})
    return _checked(Table(sid, section["title"], columns, rows, header_groups=header_groups,
                          notes=notes, sources=sources))


def _damaged(key: str, title: Any) -> Failure:
    # mit Stacktrace: ein Fehler im Zusammenbau sähe sonst aus wie kaputte Daten
    log.exception("Bericht: Ergebnis %s ließ sich nicht zusammenbauen", key)
    return Failure(key, str(title or key),
                   "Dieses gespeicherte Ergebnis ist beschädigt oder hat ein unerwartetes "
                   "Format. Den Lauf bitte neu ausführen.", "damaged")


def build_report(run: dict[str, Any]) -> Report:
    results = {item["key"]: item for item in run.get("results", [])}
    damaged = (KeyError, TypeError, ValueError, IndexError, AttributeError, QueryError)
    built: dict[str, Table | Failure] = {}
    for key, item in results.items():
        if "/" in key:
            continue
        try:
            built[key] = _block_for(item)
        except damaged:
            built[key] = _damaged(key, item.get("title") if isinstance(item, dict) else None)
    for section in (run.get("spec") or {}).get("sections", []):
        if section.get("type") == "matrix":
            try:
                built[section["id"]] = build_matrix(section, results)
            except damaged:
                built[section["id"]] = _damaged(section["id"], section.get("title"))
    tables = {key: b for key, b in built.items() if isinstance(b, Table)}
    notes: list[dict[str, str]] = []
    blocks: list[Block] = []
    title = str(run["name"])
    spec = run.get("spec") or {}
    if run["kind"] == "template":
        title = str(spec.get("title") or title)
        for section in spec.get("sections", []):
            if section.get("type") == "text":
                blocks.append(Text(section["id"], fill_placeholders(
                    section.get("markdown", ""), tables, run, notes)))
            elif section["id"] in built:
                blocks.append(built[section["id"]])
    else:
        blocks = list(built.values())
    return Report(title=title, kind=run["kind"], run_id=run["id"],
                  definition_id=run.get("definition_id"), status=run["status"],
                  started_at=run["started_at"], finished_at=run["finished_at"],
                  blocks=blocks, notes=notes)
