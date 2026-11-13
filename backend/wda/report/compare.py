"""Vergleich eines Berichts mit einem früheren Lauf.

Der frühere Lauf kommt als Punkte (Block, Zeile, Spalte → Wert); verglichen wird nur, was in
beiden Läufen dieselbe Zelle ist. Was neu ist oder fehlt, wird benannt, nie als 0 gerechnet.
"""

from __future__ import annotations

from typing import Any

from .build import format_number, format_time
from .model import Report, Table

Points = dict[tuple[str, str, str], float | None]


def delta_text(value: Any, previous: Any) -> str:
    """``+12``, ``−3``, ``±0``; leer, wenn eine der beiden Zahlen fehlt."""
    def number(x: Any) -> bool:
        return isinstance(x, int | float) and not isinstance(x, bool)

    if not number(value) or not number(previous):
        return ""
    delta = value - previous
    if isinstance(delta, float) and delta.is_integer():
        delta = int(delta)
    if delta == 0:
        return "±0"
    return ("+" if delta > 0 else "−") + format_number(abs(delta))


def _plain(value: float | None) -> int | float | None:
    return int(value) if value is not None and float(value).is_integer() else value


def apply_previous(report: Report, head: dict[str, Any], points: Points,
                   current: dict[str, Any]) -> None:
    """Trägt die Werte des früheren Laufs in den Bericht ein. ``head``/``current``: Kopfzeilen
    des früheren und des gezeigten Laufs."""
    report.compare = {"run_id": head["id"], "started_at": head["started_at"]}
    when = format_time(head["started_at"])
    if head["definition_id"] != current["definition_id"] or head["definition_id"] is None:
        report.notes.append({
            "code": "compare_other_definition",
            "text": f"Der Vergleichslauf vom {when} gehört zu einer anderen Definition "
                    f"(„{head['name']}“); verglichen wird nur, was gleich heißt."})
    elif head["definition_version"] != current["definition_version"]:
        report.notes.append({
            "code": "compare_definition_changed",
            "text": f"Die Definition wurde seit dem Vergleichslauf vom {when} geändert; "
                    "Veränderungen können auch daher rühren."})
    if head["instance"] != current["instance"]:
        report.notes.append({
            "code": "compare_other_instance",
            "text": f"Der Vergleichslauf vom {when} lief gegen eine andere Instanz."})

    by_block: dict[str, dict[str, set[str]]] = {}
    for key, row, column in points:
        by_block.setdefault(key, {}).setdefault(row, set()).add(column)
    for block in report.blocks:
        if not isinstance(block, Table):
            continue
        known = by_block.get(block.key)
        if known is None:
            block.notes.append({
                "code": "compare_block_new",
                "text": "Diese Tabelle gab es im Vergleichslauf nicht (oder sie war dort "
                        "gescheitert)."})
            for row in block.rows:
                row.previous = [None] * len(block.columns)
            continue
        seen = set()
        for row in block.rows:
            name = f"{row.kind}:{row.key}"
            seen.add(name)
            row.new = name not in known
            row.previous = [_plain(points.get((block.key, name, column.id)))
                            for column in block.columns]
        dropped = sorted(set(known) - seen)
        if dropped:
            shown = ", ".join(name.split(":", 1)[1][:60] for name in dropped[:5])
            block.notes.append({
                "code": "compare_rows_dropped",
                "text": f"{len(dropped)} {'Zeile' if len(dropped) == 1 else 'Zeilen'} des "
                        f"Vergleichslaufs gibt es nicht mehr ({shown}"
                        f"{' …' if len(dropped) > 5 else ''})."})
