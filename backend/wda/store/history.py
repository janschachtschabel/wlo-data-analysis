"""Zeitreihen aus den Punkten gespeicherter Läufe.

Gelesen werden nur die Kopfzeilen der Läufe und die schmale Tabelle ``points``; die Namen von
Zeilen und Spalten stammen aus dem jüngsten Lauf mit Ergebnissen.
"""

from __future__ import annotations

import re
import sqlite3
from collections import Counter
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
# „Was ist auffällig?“: ab wann eine Veränderung zählt, und wie viele je Definition
MIN_DELTA, MIN_PERCENT, MIN_PP, PER_DEFINITION = 10, 10.0, 5.0, 3
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


_MONTH = re.compile(r"\d{4}-\d{2}")


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
        return History._blocks_of(con, row)

    @staticmethod
    def _missed(con: sqlite3.Connection, definition: sqlite3.Row, head: sqlite3.Row
                ) -> list[dict[str, Any]]:
        """Die verfehlten Zielwerte eines Laufs: je Tabelle der Satz aus dem Bericht."""
        row = con.execute("SELECT * FROM runs WHERE id=?", (head["id"],)).fetchone()
        try:
            report = build_report(load_run(con, row))
        except (ValueError, KeyError, TypeError):
            return []
        out = []
        for table in report.tables:
            target = table.target
            if target and target.get("reached") != target.get("of"):
                out.append({
                    "definition_id": definition["id"], "name": definition["name"],
                    "kind": definition["kind"], "run_id": head["id"],
                    "started_at": head["started_at"], "block": table.title,
                    "text": next((n["text"] for n in table.notes
                                  if n.get("code") == "target"), "")})
        return out

    @staticmethod
    def _blocks_of(con: sqlite3.Connection, row: sqlite3.Row | None) -> dict[str, Any]:
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

    async def trends(self, def_id: str, limit: int = 12) -> dict[str, Any]:
        """Je Block die Gesamtzahl (Summenzeile, erste Spalte) über die jüngsten brauchbaren
        Läufe, ältester zuerst: Grundlage der kleinen Verlaufslinien in den Kacheln."""

        def fn(con: sqlite3.Connection) -> dict[str, Any]:
            fetch(con, def_id)
            where, args = _scope(def_id, None)
            heads = list(reversed(con.execute(
                _HEAD + f"WHERE {where}{_USABLE} ORDER BY seq DESC LIMIT ?",  # noqa: S608
                [*args, limit]).fetchall()))
            if len(heads) < 2:
                return {"runs": [], "blocks": {}, "labels": {}}
            # nur Läufe seit der letzten Änderung der Abfrage sind vergleichbar
            start = max((n for n in range(1, len(heads)) if changed(heads[n - 1], heads[n])),
                        default=0)
            heads = heads[start:]
            if len(heads) < 2:
                return {"runs": [], "blocks": {}, "labels": {}}
            position = {head["id"]: n for n, head in enumerate(heads)}
            # Füllgrade: Die Gesamtzeile ist dort die Grundgesamtheit, in jeder Kachel dieselbe
            shown = [b for b in self._layout(con, def_id, None)["blocks"]
                     if b["columns"] and any(r["key"] == TOTAL for r in b["rows"])
                     and not {"missing", "rate"} <= {c["id"] for c in b["columns"]}]
            first = {b["key"]: b["columns"][0]["id"] for b in shown}
            # mehrere Spalten: Die Linie zeigt die erste und sagt, welche das ist
            named = {b["key"]: b["columns"][0]["label"] for b in shown if len(b["columns"]) > 1}
            blocks: dict[str, list[float | int | None]] = {}
            marks = ",".join("?" * len(heads))
            for point in con.execute(
                    "SELECT run_id, key, column_id, value FROM points"  # noqa: S608
                    f" WHERE run_id IN ({marks}) AND row_key=?",
                    [*position, TOTAL]):
                if first.get(point["key"]) != point["column_id"] or point["value"] is None:
                    continue
                value = point["value"]
                blocks.setdefault(point["key"], [None] * len(heads))[
                    position[point["run_id"]]] = (int(value) if float(value).is_integer()
                                                  else round(value, 2))
            kept = {key: values for key, values in blocks.items()
                    if sum(v is not None for v in values) >= 2}
            # dieselbe Reihe in vielen Kacheln ist die Grundgesamtheit, nicht deren Inhalt
            shared = Counter(tuple(values) for values in kept.values())
            kept = {key: values for key, values in kept.items() if shared[tuple(values)] < 3}
            return {"runs": [{"run_id": h["id"], "started_at": h["started_at"]} for h in heads],
                    "blocks": kept,
                    "labels": {key: named[key] for key in kept if key in named}}

        return await self._db.read(fn)

    async def highlights(self, limit: int = 20, definition_id: str | None = None
                         ) -> dict[str, Any]:
        """Die größten Veränderungen zwischen den beiden jüngsten brauchbaren Läufen jeder
        Definition (ohne abweichende Instanz). Zählungen: mindestens ``MIN_DELTA`` und
        ``MIN_PERCENT`` Prozent; Prozentspalten: mindestens ``MIN_PP`` Prozentpunkte. Läufe
        vor und nach einer Änderung der Abfrage werden nicht verglichen."""

        def fn(con: sqlite3.Connection) -> dict[str, Any]:
            items: list[dict[str, Any]] = []
            watched = compared = 0
            targets: list[dict[str, Any]] = []
            chosen = (con.execute("SELECT id, name, kind, spec FROM definitions").fetchall()
                      if definition_id is None else con.execute(
                          "SELECT id, name, kind, spec FROM definitions WHERE id=?",
                          (definition_id,)).fetchall())
            for definition in chosen:
                where, args = _scope(definition["id"], None)
                heads = con.execute(
                    _HEAD + f"WHERE {where}{_USABLE} ORDER BY seq DESC LIMIT 2",  # noqa: S608
                    args).fetchall()
                if not heads:
                    continue
                runs = [con.execute("SELECT * FROM runs WHERE id=?", (h["id"],)).fetchone()
                        for h in heads]
                watched += 1
                if '"target"' in (definition["spec"] or ""):  # Zielwerte des jüngsten Laufs
                    targets += self._missed(con, definition, heads[0])
                if len(heads) < 2 or changed(heads[1], heads[0]):
                    continue
                compared += 1
                now, before = ({(r["key"], r["row_key"], r["column_id"]): r["value"]
                                for r in con.execute(
                                    "SELECT key, row_key, column_id, value FROM points"
                                    " WHERE run_id=?", (head["id"],))} for head in heads)
                # Zeilen, die es nur in einem der Läufe gibt, zählen dort als 0; nur in Blöcken,
                # die beide Läufe haben (sonst wäre ein gescheiterter Block lauter Verlust)
                both = {k for k, _, _ in now} & {k for k, _, _ in before}
                moved = []
                for where_ in {*now, *before}:
                    if where_[0] not in both:
                        continue
                    after, old = now.get(where_), before.get(where_)
                    if where_ in now and where_ in before and (after is None or old is None):
                        continue  # nicht bestimmbar ist keine Null
                    after, old = after or 0, old or 0
                    if after != old:
                        moved.append((where_, old, after))
                if not moved:
                    continue
                blocks = {b["key"]: b for b in self._blocks_of(con, runs[0])["blocks"]}
                earlier: dict[str, Any] | None = None
                found = []
                for (key, row, column), old, after in sorted(moved, key=lambda m: m[0]):
                    block = blocks.get(key)
                    if block is None:
                        continue
                    rows = [r for r in block["rows"] if r["kind"] != "total"]
                    if rows and all(_MONTH.fullmatch(r["key"]) for r in rows):
                        continue  # Zuwachs je Monat: Der laufende Monat wächst immer
                    row_label = next((r["label"] for r in block["rows"] if r["key"] == row),
                                     None)
                    if row_label is None:  # die Zeile gibt es nur im früheren Lauf
                        if earlier is None:
                            earlier = {b["key"]: b
                                       for b in self._blocks_of(con, runs[1])["blocks"]}
                        row_label = next((r["label"] for r in (earlier.get(key) or {"rows": []})
                                          ["rows"] if r["key"] == row), None)
                    column_label = next((c["label"] for c in block["columns"]
                                         if c["id"] == column), None)
                    if row_label is None or column_label is None:
                        continue
                    delta = after - old
                    if column_label.rstrip().endswith("%"):
                        if abs(delta) < MIN_PP:
                            continue
                        percent, score, unit = None, abs(delta) * 2, "pp"
                    else:
                        if abs(delta) < MIN_DELTA:
                            continue
                        percent = round(100 * delta / old, 1) if old else None
                        if percent is not None and abs(percent) < MIN_PERCENT:
                            continue
                        score, unit = (abs(percent) if percent is not None else 100.0), "count"

                    def tidy(value: float) -> float | int:
                        return int(value) if float(value).is_integer() else round(value, 1)

                    found.append((score, {
                        "definition_id": definition["id"], "name": definition["name"],
                        "kind": definition["kind"], "run_id": heads[0]["id"],
                        "previous_run_id": heads[1]["id"],
                        "started_at": heads[0]["started_at"],
                        "previous_started_at": heads[1]["started_at"],
                        "block": block["title"], "row": row_label, "column": column_label,
                        "before": tidy(old), "after": tidy(after), "delta": tidy(delta),
                        "percent": percent, "unit": unit}))
                found.sort(key=lambda item: -item[0])
                items += found[:PER_DEFINITION]
            items.sort(key=lambda item: -item[0])
            return {"items": [item for _, item in items[:limit]], "watched": watched,
                    "compared": compared, "targets": targets[:50]}

        return await self._db.read(fn)

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
