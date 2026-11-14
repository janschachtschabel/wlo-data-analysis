"""Das Modell, das alle Ausgabeformate lesen."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

Number = int | float


@dataclass
class Column:
    id: str
    label: str
    derived: bool = False


@dataclass
class Row:
    key: str
    label: str
    kind: str  # value | group | other | missing | total
    values: list[Number | None]
    level: int = 0
    percent: list[float | None] | None = None
    # Vergleich mit einem früheren Lauf: dessen Werte; ``new``: die Zeile gab es dort nicht
    previous: list[Number | None] | None = None
    new: bool = False
    # Themen je Zelle (Wissenslandkarte): je Spalte eine Liste {label, count, share}
    topics: list[list[dict[str, Any]]] | None = None


@dataclass
class Table:
    key: str
    title: str
    columns: list[Column]
    rows: list[Row]
    view: dict[str, Any] = field(default_factory=lambda: {"type": "table", "options": {}})
    header_groups: list[tuple[str, int]] | None = None  # (Beschriftung, Zahl der Spalten)
    notes: list[dict[str, str]] = field(default_factory=list)
    sources: list[dict[str, Any]] = field(default_factory=list)
    # Füllgrade: {"show": rate|missing|filled, "by_rows": bool}; sagt Grafiken, was sie zeigen
    fill: dict[str, Any] | None = None


@dataclass
class Text:
    key: str
    markdown: str


@dataclass
class Failure:
    key: str
    title: str
    detail: str
    kind: str


Block = Table | Text | Failure


@dataclass
class Report:
    title: str
    kind: str
    run_id: str
    definition_id: str | None
    status: str
    started_at: str
    finished_at: str
    blocks: list[Block]
    notes: list[dict[str, str]] = field(default_factory=list)
    compare: dict[str, Any] | None = None  # Lauf, mit dem verglichen wird

    @property
    def tables(self) -> list[Table]:
        return [b for b in self.blocks if isinstance(b, Table)]
