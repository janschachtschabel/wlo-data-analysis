"""Die Zahlen eines Laufs als schmale Zeilen (Block, Zeile, Spalte, Wert).

Zeitreihen und Vergleiche lesen nur diese Zeilen, nie die großen Ergebnis-Tabellen. Was als
Punkt zählt, bestimmt der Bericht: genau die Zellen, die er zeigt, samt Zwischensummen und
berechneten Spalten.
"""

from __future__ import annotations

import math
import sqlite3
from typing import Any

from ..report.build import build_report
from ..report.model import Table

MAX_POINTS = 60_000  # je Lauf; darüber bleibt der Lauf ohne Punkte
Point = tuple[str, str, str, float | None]


def row_key(kind: str, key: str) -> str:
    """Zeilen heißen ``art:schlüssel``, damit eine Gruppe und ein Wert sich nie decken."""
    return f"{kind}:{key}"


def points_of(run: dict[str, Any]) -> list[Point] | None:
    """Alle Zellen der Tabellen eines Laufs; ``None``, wenn es zu viele sind."""
    found: dict[tuple[str, str, str], float | None] = {}
    for block in build_report(run).blocks:
        if not isinstance(block, Table):
            continue
        for row in block.rows:
            name = row_key(row.kind, row.key)
            for column, value in zip(block.columns, row.values, strict=False):
                usable = (isinstance(value, int | float) and not isinstance(value, bool)
                          and math.isfinite(value))
                found[(block.key, name, column.id)] = float(value) if usable else None
                if len(found) > MAX_POINTS:
                    return None
    return [(*where, value) for where, value in found.items()]


def write_points(con: sqlite3.Connection, run_id: str, points: list[Point]) -> None:
    con.executemany(
        "INSERT OR REPLACE INTO points (run_id, key, row_key, column_id, value)"
        " VALUES (?,?,?,?,?)", [(run_id, *point) for point in points])
