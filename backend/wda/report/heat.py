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
    if [c.id for c in table.columns] == ["filled", "missing", "rate"]:  # Füllgrade je Feld
        return "rate"
    rest = [c for c in table.columns if c.id != "count"]
    if rest and all(c.derived and c.label.rstrip().endswith("%") for c in rest):
        return "rate"
    return "count"


def zero_of(table: Table) -> str:
    """``gap``: 0 ist eine Lücke (kein Angebot). ``plain``: 0 bleibt ungefärbt, etwa wenn
    gezählt wird, was fehlt, und 0 der gute Fall ist."""
    wanted = (table.view.get("options") or {}).get("zero")
    if wanted in ("gap", "plain"):
        return wanted
    return "plain" if (table.fill or {}).get("show") == "missing" else "gap"


def classes(table: Table) -> list[list[str]]:
    """Klasse je Zelle: ``h1``…``h6`` nach der Wurzel des Anteils am größten Wert, ``gap``
    für 0, leer für unbekannt. Summen- und „ohne Wert“-Zeilen bleiben ungefärbt."""
    scale = scale_of(table)
    # „Anzahl“ neben anderen Spalten bleibt ungefärbt; die Ampel färbt nur Prozent-Spalten
    skip = [(len(table.columns) > 1 and c.id == "count")
            or (scale == "rate" and not c.label.rstrip().endswith("%")) for c in table.columns]
    numbers = [v for row in table.rows if row.kind not in _PLAIN
               for v, out in zip(row.values, skip, strict=False)
               if not out and isinstance(v, int | float) and not isinstance(v, bool)]
    top = max(numbers, default=0)
    zero = zero_of(table)
    tops = None
    if (table.view.get("options") or {}).get("scale_by") == "column":
        # Skala je Spalte: nebeneinander stehen Größen, die sich nicht vergleichen lassen
        tops = [max((r.values[n] for r in table.rows
                     if r.kind not in _PLAIN and n < len(r.values)
                     and isinstance(r.values[n], int | float)
                     and not isinstance(r.values[n], bool)), default=0)
                for n in range(len(table.columns))]
    out = []
    for row in table.rows:
        line = []
        for n, (out_of_scale, value) in enumerate(zip(skip, row.values, strict=False)):
            if tops is not None:
                top = tops[n]
            if (row.kind in _PLAIN or out_of_scale or not isinstance(value, int | float)
                    or isinstance(value, bool)):
                line.append("")
            elif scale == "rate":
                line.append("r-low" if value < 50 else "r-mid" if value < 80 else "r-high")
            elif value <= 0 or top <= 0:
                line.append("gap" if zero == "gap" and value == 0 else "")
            else:
                line.append(f"h{max(1, math.ceil(math.sqrt(value / top) * STEPS))}")
        out.append(line)
    return out


def legend(table: Table) -> str:
    if scale_of(table) == "rate":
        return ('<p class="legend"><span class="key r-low"></span> unter 50 % '
                '<span class="key r-mid"></span> 50 bis unter 80 % '
                '<span class="key r-high"></span> ab 80 %</p>')
    by_column = (table.view.get("options") or {}).get("scale_by") == "column"
    return ('<p class="legend">'
            + ('<span class="key gap"></span> 0 (Lücke) ' if zero_of(table) == "gap" else "")
            + '<span class="key h1"></span> wenig <span class="key h3"></span> '
            '<span class="key h6"></span> viel (im Verhältnis zum größten Wert der '
            + ("Spalte" if by_column else "Tabelle") + ')</p>')


CSS = """
table { print-color-adjust: exact; -webkit-print-color-adjust: exact; }
tr td.h1 { background: #edf3fb; } tr td.h2 { background: #dbe7f7; }
tr td.h3 { background: #c5d8f2; } tr td.h4 { background: #abc6ec; }
tr td.h5 { background: #8fb3e5; } tr td.h6 { background: #729fdd; }
tr td.gap { background: repeating-linear-gradient(135deg, #f3c9c5 0 4px, #fff 4px 8px); }
tr td.r-low { background: #f2c4c0; border-left: 3px solid #b3261e; }
tr td.r-mid { background: #f8e7b0; } tr td.r-high { background: #c9e8d5; }
.legend { font-size: 8pt; color: #444; margin: .2rem 0; }
.key { display: inline-block; width: 10px; height: 10px; border: 1px solid #999;
       margin: 0 2px 0 8px; vertical-align: middle; }
.key.h1 { background: #edf3fb; } .key.h3 { background: #c5d8f2; } .key.h6 { background: #729fdd; }
.key.gap { background: repeating-linear-gradient(135deg, #f3c9c5 0 4px, #fff 4px 8px); }
.key.r-low { background: #f2c4c0; } .key.r-mid { background: #f8e7b0; }
.key.r-high { background: #c9e8d5; }
"""
