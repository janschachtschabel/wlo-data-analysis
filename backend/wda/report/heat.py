"""Eingefärbte Tabelle („Heatmap“): dieselbe Regel wie in der Oberfläche
(frontend/src/app/shared/heat.ts)."""

from __future__ import annotations

import math

from .model import Table

STEPS = 6
_PLAIN = {"total", "missing"}


def scale_of(table: Table) -> str:
    """``rate`` (Ampel für Anteile in Prozent) oder ``count`` (Skala über die Zählungen)."""
    wanted = (table.view.get("options") or {}).get("scale")
    if wanted in ("rate", "count"):
        return wanted
    rest = [c for c in table.columns if c.id != "count"]
    if rest and all(c.derived and c.label.rstrip().endswith("%") for c in rest):
        return "rate"
    return "count"


def classes(table: Table) -> list[list[str]]:
    """Klasse je Zelle: ``h1``…``h6`` nach der Wurzel des Anteils am größten Wert, ``gap``
    für 0, leer für unbekannt. Summen- und „ohne Wert“-Zeilen bleiben ungefärbt; bei der
    Ampel auch die Spalte „Anzahl“."""
    scale = scale_of(table)
    numbers = [v for row in table.rows if row.kind not in _PLAIN for v in row.values
               if isinstance(v, int | float) and not isinstance(v, bool)]
    top = max(numbers, default=0)
    out = []
    for row in table.rows:
        line = []
        for column, value in zip(table.columns, row.values, strict=False):
            if (row.kind in _PLAIN or not isinstance(value, int | float)
                    or isinstance(value, bool)):
                line.append("")
            elif scale == "rate":
                line.append("" if column.id == "count" else
                            "r-low" if value < 50 else "r-mid" if value < 80 else "r-high")
            elif value <= 0 or top <= 0:
                line.append("gap")
            else:
                line.append(f"h{max(1, math.ceil(math.sqrt(value / top) * STEPS))}")
        out.append(line)
    return out


def legend(table: Table) -> str:
    if scale_of(table) == "rate":
        return ('<p class="legend"><span class="key r-low"></span> unter 50 % '
                '<span class="key r-mid"></span> 50 bis unter 80 % '
                '<span class="key r-high"></span> ab 80 %</p>')
    return ('<p class="legend"><span class="key gap"></span> 0 (Lücke) '
            '<span class="key h1"></span> wenig <span class="key h3"></span> '
            '<span class="key h6"></span> viel</p>')


CSS = """
td.h1 { background: #edf3fb; } td.h2 { background: #dbe7f7; } td.h3 { background: #c5d8f2; }
td.h4 { background: #abc6ec; } td.h5 { background: #8fb3e5; } td.h6 { background: #729fdd; }
td.gap { background: repeating-linear-gradient(135deg, #f3c9c5 0 4px, #fff 4px 8px); }
td.r-low { background: #f2c4c0; border-left: 3px solid #b3261e; }
td.r-mid { background: #f8e7b0; } td.r-high { background: #c9e8d5; }
.legend { font-size: 8pt; color: #444; margin: .2rem 0; }
.key { display: inline-block; width: 10px; height: 10px; border: 1px solid #999;
       margin: 0 2px 0 8px; vertical-align: middle; }
.key.h1 { background: #edf3fb; } .key.h3 { background: #c5d8f2; } .key.h6 { background: #729fdd; }
.key.gap { background: repeating-linear-gradient(135deg, #f3c9c5 0 4px, #fff 4px 8px); }
.key.r-low { background: #f2c4c0; } .key.r-mid { background: #f8e7b0; }
.key.r-high { background: #c9e8d5; }
"""
