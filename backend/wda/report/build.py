"""Baut aus einem gespeicherten Lauf den Bericht."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from ..engine.engine import SCHEMA_VERSION
from .model import Block, Column, Failure, Report, Row, Table, Text

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
                percent=r.get("percent")) for r in result["rows"]]
    # Gesamtzeile: die Zählung über alle Materialien, keine Summe der Zeilen
    rows.append(Row("__total__", "Gesamt", "total", [c.get("total") for c in result["columns"]]))
    prov = result.get("provenance", {})
    inst = prov.get("instance") or {}
    return Table(
        key, title, columns, rows,
        view=view or {"type": "table", "options": {}},
        notes=list(prov.get("notes") or []),
        sources=[{
            "label": inst.get("id") or result.get("instance"), "kind": "query",
            "base_url": inst.get("base_url"), "fetched_at": prov.get("fetched_at"),
            "oldest_fetched_at": prov.get("oldest_fetched_at"),
            "queries": prov.get("queries"),
            "filters": describe_filters(prov.get("filters") or []),
        }])


def _lookup(tables: dict[str, Table], run: dict[str, Any], path: str) -> str | None:
    if path == "run.date":
        return format_time(run["started_at"])
    if path == "run.name":
        return str(run["name"])
    if path == "run.instance":
        labels = sorted({str(s["label"]) for t in tables.values() for s in t.sources
                         if s.get("kind") == "query" and s.get("label")})
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


def build_report(run: dict[str, Any]) -> Report:
    results = {item["key"]: item for item in run.get("results", [])}
    built: dict[str, Table | Failure] = {key: _block_for(item) for key, item in results.items()}
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
