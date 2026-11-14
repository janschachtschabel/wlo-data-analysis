"""Grafiken als SVG, ohne Skript und ohne externe Ressourcen. HTML und PDF zeigen dasselbe."""

from __future__ import annotations

import math
from html import escape

from .build import format_number
from .model import Table

PALETTE = ["#1f6fb2", "#e07b39", "#3a9d5d", "#b8445a", "#7a5cc2", "#8c8c3a", "#3aa7b8",
           "#c26aa8", "#6b7280", "#a3622b"]
MAX_ITEMS = 25
WIDTH = 720


def _text(x: float, y: float, content: str, anchor: str = "start", size: int = 11,
          color: str = "#222") -> str:
    return (f'<text x="{x:.1f}" y="{y:.1f}" font-size="{size}" text-anchor="{anchor}" '
            f'fill="{color}">{escape(content)}</text>')


def _short(label: str, limit: int = 34) -> str:
    return label if len(label) <= limit else label[: limit - 1] + "…"


def _svg(height: float, body: list[str], title: str) -> str:
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {WIDTH} {height:.0f}" '
            f'width="{WIDTH}" height="{height:.0f}" role="img" font-family="sans-serif" '
            f'aria-label="{escape(title, quote=True)}">' + "".join(body) + "</svg>")


def _legend(labels: list[str], y: float) -> list[str]:
    out, x = [], 10.0
    for pos, label in enumerate(labels):
        text = _short(label, 28)
        out.append(f'<rect x="{x:.1f}" y="{y - 9:.1f}" width="10" height="10" '
                   f'fill="{PALETTE[pos % len(PALETTE)]}"/>')
        out.append(_text(x + 14, y, text))
        x += 26 + 6.2 * len(text)
        if x > WIDTH - 150:
            x, y = 10.0, y + 16
    return out


def _data(table: Table) -> tuple[list[str], list[list[float]], list[str]]:
    """Zeilen mit Zahlen (ohne Gesamt- und Gruppenzeilen), gezählte Spalten."""
    cols = [pos for pos, c in enumerate(table.columns) if not c.derived] or list(
        range(len(table.columns)))
    if table.fill:  # dieselbe Auswahl wie in der Oberfläche (charts/options.ts)
        if table.fill.get("by_rows"):
            cols = list(range(1, len(table.columns)))  # je Feld, ohne die Anzahl
        elif (table.view or {}).get("type") == "stacked_bar":
            cols = [0, 1]  # mit Wert, ohne Wert
        else:
            cols = [2]  # Füllgrad
    rows = [r for r in table.rows if r.kind in ("value", "other", "missing")
            or (r.kind == "group" and not any(x.level for x in table.rows))]
    rows = [r for r in rows if any(isinstance(r.values[c], int | float) for c in cols)]
    rows = rows[:MAX_ITEMS]
    values = [[float(r.values[c]) if isinstance(r.values[c], int | float)
               and math.isfinite(r.values[c]) and r.values[c] > 0 else 0.0 for c in cols]
              for r in rows]
    return [r.label for r in rows], values, [table.columns[c].label for c in cols]


def bar_chart(table: Table, stacked: bool = False) -> str | None:
    labels, values, series = _data(table)
    if not labels:
        return None
    if not stacked:
        values, series = [[row[0]] for row in values], series[:1]
    top = max((sum(row) for row in values), default=0) or 1
    left, right, row_h = 230, 70, 22
    legend_h = 24 if len(series) > 1 else 0
    body = _legend(series, 14) if len(series) > 1 else []
    for pos, (label, row) in enumerate(zip(labels, values, strict=True)):
        y = legend_h + 8 + pos * row_h
        body.append(_text(left - 8, y + 14, _short(label), "end"))
        x = float(left)
        for idx, value in enumerate(row):
            width = (WIDTH - left - right) * value / top
            body.append(f'<rect x="{x:.1f}" y="{y + 3}" width="{width:.1f}" height="15" '
                        f'fill="{PALETTE[idx % len(PALETTE)]}"/>')
            x += width
        body.append(_text(x + 5, y + 14, format_number(sum(row))))
    return _svg(legend_h + 16 + len(labels) * row_h, body, table.title)


def pie_chart(table: Table) -> str | None:
    labels, values, _ = _data(table)
    pairs = [(label, row[0]) for label, row in zip(labels, values, strict=True) if row[0] > 0]
    if not pairs:
        return None
    pairs.sort(key=lambda p: -p[1])
    if len(pairs) > 9:
        pairs = pairs[:8] + [("Übrige", sum(v for _, v in pairs[8:]))]
    total = sum(v for _, v in pairs)
    cx, cy, radius, angle = 130.0, 130.0, 110.0, -math.pi / 2
    body = []
    for pos, (label, value) in enumerate(pairs):
        share = value / total
        color = PALETTE[pos % len(PALETTE)]
        if share >= 0.9999:
            body.append(f'<circle cx="{cx}" cy="{cy}" r="{radius}" fill="{color}"/>')
        else:
            end = angle + share * 2 * math.pi
            x1, y1 = cx + radius * math.cos(angle), cy + radius * math.sin(angle)
            x2, y2 = cx + radius * math.cos(end), cy + radius * math.sin(end)
            body.append(f'<path d="M{cx},{cy} L{x1:.2f},{y1:.2f} A{radius},{radius} 0 '
                        f'{1 if share > 0.5 else 0} 1 {x2:.2f},{y2:.2f} Z" fill="{color}"/>')
            angle = end
        y = 30 + pos * 22
        body.append(f'<rect x="280" y="{y - 10}" width="12" height="12" fill="{color}"/>')
        body.append(_text(298, y, f"{_short(label, 40)}: {format_number(value)} "
                                  f"({format_number(share * 100, 1)} %)"))
    return _svg(max(260, 40 + len(pairs) * 22), body, table.title)


def line_chart(table: Table) -> str | None:
    labels, values, series = _data(table)
    if len(labels) < 2:
        return None
    top = max((v for row in values for v in row), default=0) or 1
    left, right, top_pad, height = 60, 20, 30, 260
    step = (WIDTH - left - right) / (len(labels) - 1)
    body = _legend(series, 14)
    body.append(f'<line x1="{left}" y1="{top_pad + height}" x2="{WIDTH - right}" '
                f'y2="{top_pad + height}" stroke="#999"/>')
    body.append(_text(left - 6, top_pad + 4, format_number(top), "end", 10))
    body.append(_text(left - 6, top_pad + height, "0", "end", 10))
    for idx in range(len(series)):
        points = " ".join(
            f"{left + pos * step:.1f},{top_pad + height - height * row[idx] / top:.1f}"
            for pos, row in enumerate(values))
        body.append(f'<polyline points="{points}" fill="none" stroke-width="2" '
                    f'stroke="{PALETTE[idx % len(PALETTE)]}"/>')
    every = max(1, len(labels) // 8)
    for pos, label in enumerate(labels):
        if pos % every == 0:
            body.append(_text(left + pos * step, top_pad + height + 16, _short(label, 14),
                              "middle", 10))
    return _svg(top_pad + height + 30, body, table.title)


def chart_for(table: Table) -> str | None:
    kind = (table.view or {}).get("type", "table")
    if kind == "bar":
        return bar_chart(table)
    if kind == "stacked_bar":
        return bar_chart(table, stacked=True)
    if kind == "pie":
        return pie_chart(table)
    if kind == "line":
        return line_chart(table)
    return None
