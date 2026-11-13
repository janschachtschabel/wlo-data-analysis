"""Zeitreihen aus den Punkten gespeicherter Läufe.

Gelesen werden nur die Kopfzeilen der Läufe und die schmale Tabelle ``points``; die Namen von
Zeilen und Spalten stammen aus dem jüngsten Lauf mit Ergebnissen.
"""

from __future__ import annotations

import re
import sqlite3
from datetime import UTC, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from ..errors import QueryError
from ..report.build import build_report
from ..report.model import Table
from .db import Database
from .definitions import NotFound, fetch
from .points import row_key
from .runner import load_run

MAX_SERIES = 8
MAX_RUNS = 1000
TOTAL = row_key("total", "__total__")
_WHEN = re.compile(r"^\d{4}-\d{2}-\d{2}(T\d{2}:\d{2}(:\d{2}(\.\d{1,6})?)?(Z|[+-]\d{2}:\d{2})?)?$")


def _scope(def_id: str, instance: str | None) -> tuple[str, list[Any]]:
    """Läufe einer Definition gegen eine Instanz; ohne Angabe die Läufe ohne Abweichung."""
    if instance is None:
        return "definition_id=? AND instance IS NULL", [def_id]
    return "definition_id=? AND instance=?", [def_id, instance]


def bound(value: str | None, what: str, tz: ZoneInfo, end: bool = False) -> str | None:
    """Ein Datum oder Zeitpunkt als UTC in der Schreibweise der gespeicherten Läufe. Ein
    bloßer Tag meint den Kalendertag in der Zeitzone der Anwendung: seinen Anfang, mit
    ``end`` sein Ende. Ein Zeitpunkt ohne Zone gilt ebenfalls in dieser Zeitzone."""
    if value is None:
        return None
    if not _WHEN.match(value):
        raise QueryError(f"'{what}' erwartet ein Datum wie 2026-11-13.")
    try:
        if len(value) == 10:
            day = datetime.strptime(value, "%Y-%m-%d").date()  # noqa: DTZ007
            moment = datetime.combine(day + timedelta(days=1) if end else day, time(), tz)
            if end:
                moment -= timedelta(milliseconds=1)
        else:
            moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if moment.tzinfo is None:
                moment = moment.replace(tzinfo=tz)
    except ValueError:
        raise QueryError(f"'{what}' ist kein gültiges Datum.") from None
    return moment.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def changed(before: Any, after: Any) -> bool:
    """Haben zwei Läufe (Kopfzeilen) Verschiedenes gefragt? Maßgeblich ist die Kurzform der
    ausgeführten Abfragen; fehlt sie bei einem älteren Lauf, die Version der Definition."""
    if before["fingerprint"] is not None and after["fingerprint"] is not None:
        return before["fingerprint"] != after["fingerprint"]
    return before["definition_version"] != after["definition_version"]


_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_HEAD = ("SELECT seq, id, definition_id, name, definition_version, instance, started_at,"
         " fingerprint, status FROM runs ")
# „der vorige Lauf“ ist einer, mit dem sich vergleichen lässt: nicht gescheitert, mit Zahlen
_USABLE = " AND status != 'failed' AND EXISTS (SELECT 1 FROM points WHERE run_id = runs.id)"


def compare_target(con: sqlite3.Connection, run_id: str, target: str, tz: ZoneInfo
                   ) -> tuple[dict[str, Any], dict[str, Any], dict[tuple[str, str, str], Any]]:
    """Kopfzeilen des gezeigten und des Vergleichslaufs und dessen Punkte. ``target``:
    ``previous`` (voriger brauchbarer Lauf derselben Definition gegen dieselbe Instanz), ein
    Datum (letzter solcher Lauf vor dem gezeigten, an oder vor diesem Tag) oder die ID eines
    Laufs."""
    current = con.execute(_HEAD + "WHERE id=?", (run_id,)).fetchone()
    if current is None:
        raise NotFound("Den Lauf gibt es nicht.")
    if target == run_id:
        raise QueryError("Ein Lauf lässt sich nicht mit sich selbst vergleichen.")
    found = None
    if target == "previous" or _DAY.match(target):
        if current["definition_id"] is not None:
            where, args = _scope(current["definition_id"], current["instance"])
            where += " AND seq < ?" + _USABLE
            args.append(current["seq"])
            if target != "previous":
                where += " AND started_at <= ?"
                args.append(bound(target, "compare", tz, end=True))
            found = con.execute(_HEAD + f"WHERE {where} ORDER BY seq DESC LIMIT 1",  # noqa: S608
                                args).fetchone()
    else:
        found = con.execute(_HEAD + "WHERE id=?", (target,)).fetchone()
    if found is None:
        raise NotFound("Dazu gibt es keinen früheren Lauf zum Vergleichen.")
    points = {(r["key"], r["row_key"], r["column_id"]): r["value"] for r in con.execute(
        "SELECT key, row_key, column_id, value FROM points WHERE run_id=?", (found["id"],))}
    return dict(current), dict(found), points


class History:
    def __init__(self, db: Database, tz: ZoneInfo) -> None:
        self._db = db
        self._tz = tz

    @staticmethod
    def _layout(con: sqlite3.Connection, def_id: str, instance: str | None
                ) -> dict[str, Any]:
        """Blöcke, Zeilen und Spalten des jüngsten Laufs, der Ergebnisse hat."""
        where, args = _scope(def_id, instance)
        row = con.execute(
            f"SELECT * FROM runs WHERE {where} AND EXISTS (SELECT 1 FROM run_results"  # noqa: S608
            " WHERE run_id=runs.id AND result IS NOT NULL) ORDER BY seq DESC LIMIT 1",
            args).fetchone()
        if row is None:
            return {"run_id": None, "blocks": []}
        try:
            report = build_report(load_run(con, row))
        except (ValueError, KeyError, TypeError):
            return {"run_id": row["id"], "blocks": []}
        def columns(block: Table) -> list[dict[str, str]]:
            # Bei Blöcken nebeneinander heißen die Spalten gleich; der Block gehört zum Namen.
            groups = [name for name, span in (block.header_groups or []) for _ in range(span)]
            return [{"id": c.id,
                     "label": f"{groups[n]} · {c.label}" if n < len(groups) and groups[n]
                     else c.label}
                    for n, c in enumerate(block.columns)]

        blocks = [{"key": block.key, "title": block.title, "columns": columns(block),
                   "rows": [{"key": row_key(r.kind, r.key), "label": r.label, "kind": r.kind}
                            for r in block.rows]}
                  for block in report.blocks if isinstance(block, Table)]
        return {"run_id": row["id"], "blocks": blocks}

    async def keys(self, def_id: str, instance: str | None = None) -> dict[str, Any]:
        def fn(con: sqlite3.Connection) -> dict[str, Any]:
            fetch(con, def_id)
            return self._layout(con, def_id, instance)

        return await self._db.read(fn)

    async def compare(self, run_id: str, target: str):
        return await self._db.read(lambda con: compare_target(con, run_id, target, self._tz))

    async def has_points(self, run_id: str) -> bool:
        return await self._db.read(lambda con: con.execute(
            "SELECT 1 FROM points WHERE run_id=? LIMIT 1", (run_id,)).fetchone() is not None)

    async def series(self, def_id: str, *, key: str | None = None,
                     rows: list[str] | None = None, columns: list[str] | None = None,
                     instance: str | None = None, start: str | None = None,
                     end: str | None = None, limit: int = 200) -> dict[str, Any]:
        start = bound(start, "from", self._tz)
        end = bound(end, "to", self._tz, end=True)  # der genannte Tag gehört dazu
        limit = max(1, min(limit, MAX_RUNS))

        def fn(con: sqlite3.Connection) -> dict[str, Any]:
            fetch(con, def_id)
            layout = self._layout(con, def_id, instance)
            if not layout["blocks"]:
                return {"series": [], "points": [], "notes": [{
                    "code": "no_runs",
                    "text": "Für diese Definition gibt es noch keinen gespeicherten Lauf mit "
                            "Ergebnissen."}]}
            blocks = {b["key"]: b for b in layout["blocks"]}
            block = blocks.get(key) if key is not None else layout["blocks"][0]
            if block is None:
                raise QueryError(f"Den Block '{key[:60]}' gibt es im jüngsten Lauf nicht. "  # type: ignore[index]
                                 f"Vorhanden: {', '.join(list(blocks)[:10])}.")
            row_names = {r["key"]: r["label"] for r in block["rows"]}
            column_names = {c["id"]: c["label"] for c in block["columns"]}
            wanted_rows = rows or [TOTAL if TOTAL in row_names else block["rows"][0]["key"]]
            wanted_columns = columns or [c["id"] for c in block["columns"]][
                :MAX_SERIES if len(wanted_rows) == 1 else 1]
            for name in wanted_rows:
                if name not in row_names:
                    raise QueryError(f"Die Zeile '{name[:80]}' gibt es im jüngsten Lauf nicht.")
            for name in wanted_columns:
                if name not in column_names:
                    raise QueryError(f"Die Spalte '{name[:80]}' gibt es im jüngsten Lauf nicht.")
            chosen = [(r, c) for r in dict.fromkeys(wanted_rows)
                      for c in dict.fromkeys(wanted_columns)]
            if len(chosen) > MAX_SERIES:
                raise QueryError(f"Eine Zeitreihe zeigt höchstens {MAX_SERIES} Reihen "
                                 f"(gewählt: {len(chosen)}).")

            where, args = _scope(def_id, instance)
            if start is not None:
                where += " AND started_at >= ?"
                args.append(start)
            if end is not None:
                where += " AND started_at <= ?"
                args.append(end)
            heads = list(reversed(con.execute(
                "SELECT seq, id, started_at, status, definition_version, origin, fingerprint,"  # noqa: S608
                " EXISTS (SELECT 1 FROM points WHERE run_id = runs.id) AS has_points FROM runs"
                f" WHERE {where} ORDER BY seq DESC LIMIT ?", [*args, limit]).fetchall()))
            position = {head["id"]: n for n, head in enumerate(heads)}
            values: list[list[float | None]] = [[None] * len(chosen) for _ in heads]
            if heads:
                low, high = heads[0]["seq"], heads[-1]["seq"]
                scope_where, scope_args = _scope(def_id, instance)
                for index, (row, column) in enumerate(chosen):
                    for point in con.execute(
                            "SELECT run_id, value FROM points WHERE run_id IN ("  # noqa: S608
                            f"SELECT id FROM runs WHERE {scope_where} AND seq BETWEEN ? AND ?)"
                            " AND key=? AND row_key=? AND column_id=?",
                            [*scope_args, low, high, block["key"], row, column]):
                        at = position.get(point["run_id"])
                        if at is not None:
                            value = point["value"]
                            values[at][index] = (int(value) if value is not None
                                                 and float(value).is_integer() else value)
            single_row, single_column = len(set(r for r, _ in chosen)) == 1, len(
                set(c for _, c in chosen)) == 1
            series = []
            for row, column in chosen:
                if single_column and not single_row:
                    label = row_names[row]
                elif single_row and not single_column:
                    label = column_names[column]
                elif single_row and single_column:
                    label = (row_names[row] if row != TOTAL or len(column_names) == 1
                             else column_names[column])
                else:
                    label = f"{row_names[row]} · {column_names[column]}"
                series.append({"key": block["key"], "row": row, "column": column,
                               "label": label, "row_label": row_names[row],
                               "column_label": column_names[column]})
            notes = []
            marks = [n > 0 and changed(heads[n - 1], head) for n, head in enumerate(heads)]
            if any(marks):
                notes.append({
                    "code": "definition_changed",
                    "text": "Die Definition oder ein Filter, den sie benutzt, wurde in diesem "
                            "Zeitraum geändert; Zahlen vor und nach der Änderung sind nicht "
                            "ohne Weiteres vergleichbar."})
            empty = sum(1 for h in heads if not h["has_points"] and h["status"] != "failed")
            if empty:
                notes.append({
                    "code": "runs_without_points",
                    "text": f"{empty} {'Lauf hat' if empty == 1 else 'Läufe haben'} keine "
                            "Zahlen für die Historie (zu groß oder nicht lesbar) und "
                            "erscheinen als Lücke."})
            return {"block": {"key": block["key"], "title": block["title"]}, "series": series,
                    "points": [{"run_id": h["id"], "started_at": h["started_at"],
                                "status": h["status"],
                                "definition_version": h["definition_version"],
                                "changed": marks[n],
                                "origin": h["origin"], "values": values[n]}
                               for n, h in enumerate(heads)],
                    "notes": notes}

        return await self._db.read(fn)
