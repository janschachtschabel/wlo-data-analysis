"""Die fünf Ausgabeformate. Alle lesen nur das Berichtsmodell."""

from __future__ import annotations

import csv
import dataclasses
import io
import json
import re
from html import escape
from typing import Any

from .build import format_number, format_time
from .charts import chart_for
from .compare import delta_text
from .model import Failure, Report, Row, Table, Text
from .text import markdown_to_html

KIND_LABELS = {"metric": "Kennzahl", "dashboard": "Dashboard", "template": "Bericht"}
STATUS_LABELS = {"ok": "vollständig", "partial": "teilweise (einzelne Abfragen gescheitert)",
                 "failed": "gescheitert"}
FORMATS = {
    "json": ("application/json", "json"), "csv": ("text/csv; charset=utf-8", "csv"),
    "md": ("text/markdown; charset=utf-8", "md"), "html": ("text/html; charset=utf-8", "html"),
    "pdf": ("application/pdf", "pdf")}


def _row_label(row: Row) -> str:
    return ("  " * row.level) + row.label + (" (neu)" if row.new else "")


def _cell(row: Row, pos: int) -> str:
    text = format_number(row.values[pos])
    if row.percent and pos < len(row.percent) and row.percent[pos] is not None:
        text += f" ({format_number(row.percent[pos], 1)} %)"
    if row.previous is not None and pos < len(row.previous):
        change = delta_text(row.values[pos], row.previous[pos])
        if change:
            text += f" ({change})"
    return text


def source_lines(table: Table) -> list[str]:
    lines = []
    for src in table.sources:
        if src.get("kind") == "manual":
            line = f"{src['label']}: Handeintrag"
            if src.get("as_of"):
                line += f", Stand {src['as_of']}"
            if src.get("note"):
                line += f" ({src['note']})"
        elif src.get("kind") == "failed":
            line = f"{src['label']}: kein Ergebnis"
        else:
            line = f"{src['label']}: "
            if src.get("kind") == "external":
                line += f"{src.get('service', 'externe Quelle')}, "
            elif src.get("base_url"):
                line += f"{src['base_url']}, "
            line += f"abgerufen {format_time(src.get('fetched_at'))}"
            if src.get("queries"):
                line += (", 1 Abfrage" if src["queries"] == 1
                         else f", {src['queries']} Abfragen")
            if src.get("filters"):
                line += f", Filter: {src['filters']}"
        lines.append(line)
    return lines


def _meta(report: Report) -> str:
    text = (f"{KIND_LABELS.get(report.kind, report.kind)} · Lauf vom "
            f"{format_time(report.started_at)} · {STATUS_LABELS.get(report.status, report.status)}")
    if report.compare:
        text += (f" · Vergleich mit dem Lauf vom {format_time(report.compare['started_at'])} "
                 "(Veränderung in Klammern)")
    return text


# ---- JSON ----------------------------------------------------------------------------

REPORT_VERSION = 1


def report_data(report: Report, with_html: bool = False) -> dict[str, Any]:
    """Das Berichtsmodell als JSON-fähige Struktur. ``with_html`` legt Textabschnitten das
    fertige, maskierte HTML bei (für die Oberfläche)."""
    def block(item: Any) -> dict[str, Any]:
        data = dataclasses.asdict(item)
        data["type"] = type(item).__name__.lower()
        if with_html and isinstance(item, Text):
            data["html"] = markdown_to_html(item.markdown)
        return data

    return {
        "report_version": REPORT_VERSION,
        "title": report.title, "kind": report.kind, "run_id": report.run_id,
        "definition_id": report.definition_id, "status": report.status,
        "started_at": report.started_at, "finished_at": report.finished_at,
        "compare": report.compare,
        "notes": report.notes, "blocks": [block(b) for b in report.blocks]}


def to_json(report: Report) -> bytes:
    return json.dumps(report_data(report), ensure_ascii=False, indent=2, allow_nan=False,
                      default=str).encode("utf-8")


# ---- CSV -----------------------------------------------------------------------------

def _safe(text: Any) -> Any:
    """Text, den eine Tabellenkalkulation als Formel lesen würde, wird entschärft."""
    if isinstance(text, str) and text.lstrip()[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + text
    return text


def to_csv(report: Report, delimiter: str = ";") -> bytes:
    out = io.StringIO()
    writer = csv.writer(out, delimiter=delimiter, lineterminator="\r\n")
    decimal_comma = delimiter == ";"

    def number(value: Any) -> Any:
        if value is None or isinstance(value, bool):
            return ""
        if isinstance(value, float):
            if not value == value or value in (float("inf"), float("-inf")):
                return ""
            text = repr(round(value, 6))
            return text.replace(".", ",") if decimal_comma else text
        return value if isinstance(value, int) else ""

    writer.writerow([_safe(report.title)])
    writer.writerow([_safe(_meta(report))])
    for item in report.blocks:
        if isinstance(item, Text):
            continue
        writer.writerow([])
        writer.writerow([_safe(item.title)])
        if isinstance(item, Failure):
            writer.writerow(["Fehler", _safe(item.detail)])
            continue
        if item.header_groups:
            line: list[Any] = [""]
            for label, span in item.header_groups:
                line += [_safe(label)] + [""] * (span - 1)
            writer.writerow(line)
        with_percent = any(r.percent for r in item.rows)
        with_previous = report.compare is not None
        header: list[Any] = [""]
        for col in item.columns:
            header.append(_safe(col.label))
            if with_percent:
                header.append(_safe(col.label + " (%)"))
            if with_previous:
                header.append(_safe(col.label + " (vorher)"))
        writer.writerow(header)
        for row in item.rows:
            line = [_safe(_row_label(row))]
            for pos in range(len(item.columns)):
                line.append(number(row.values[pos]))
                if with_percent:
                    line.append(number(row.percent[pos] if row.percent else None))
                if with_previous:
                    line.append(number(row.previous[pos] if row.previous else None))
            writer.writerow(line)
        for note in item.notes:
            writer.writerow(["Hinweis", _safe(note["text"])])
        for src in source_lines(item):
            writer.writerow(["Quelle", _safe(src)])
    return ("\ufeff" + out.getvalue()).encode("utf-8")


# ---- Markdown ------------------------------------------------------------------------

def _md(text: Any) -> str:
    out = str(text).replace("\\", "\\\\").replace("|", "\\|")
    out = re.sub(r"[\x00-\x1f\x7f\u0085\u2028\u2029]+", " ", out)
    for ch in "<>[]`*_":
        out = out.replace(ch, "\\" + ch)
    return out


def to_markdown(report: Report) -> bytes:
    lines = [f"# {_md(report.title)}", "", _md(_meta(report)), ""]
    for item in report.blocks:
        if isinstance(item, Text):
            lines += [item.markdown.strip(), ""]
            continue
        lines += [f"## {_md(item.title)}", ""]
        if isinstance(item, Failure):
            lines += [f"**Fehler:** {_md(item.detail)}", ""]
            continue
        if item.header_groups:
            groups = " · ".join(f"{_md(label)} ({span} Spalten)"
                                for label, span in item.header_groups)
            lines += [f"Spaltenblöcke: {groups}", ""]
            prefix = [label for label, span in item.header_groups for _ in range(span)]
        else:
            prefix = [""] * len(item.columns)
        heads = [(f"{_md(p)}: " if p else "") + _md(c.label) + (" (berechnet)" if c.derived
                                                                else "")
                 for p, c in zip(prefix, item.columns, strict=True)]
        lines.append("| | " + " | ".join(heads) + " |")
        lines.append("|---|" + "---:|" * len(item.columns))
        for row in item.rows:
            label = ("&nbsp;&nbsp;" * row.level) + _md(row.label)
            if row.kind in ("group", "total"):
                label = f"**{label}**"
            cells = [_cell(row, pos) for pos in range(len(item.columns))]
            lines.append(f"| {label} | " + " | ".join(cells) + " |")
        lines.append("")
        lines += [f"- Hinweis: {_md(n['text'])}" for n in item.notes]
        lines += [f"- Quelle: {_md(s)}" for s in source_lines(item)]
        lines.append("")
    lines += [f"- Hinweis: {_md(n['text'])}" for n in report.notes]
    return ("\n".join(lines).rstrip() + "\n").encode("utf-8")


# ---- HTML und PDF --------------------------------------------------------------------

_CSS = """
@page { size: A4 landscape; margin: 12mm; }
body { font-family: "DejaVu Sans", Arial, Helvetica, sans-serif; font-size: 10pt; color: #1a1a1a;
       margin: 1.5rem auto; max-width: 1200px; padding: 0 1rem; }
h1 { font-size: 18pt; margin: 0 0 .2rem; } h2 { font-size: 13pt; margin: 1.4rem 0 .4rem; }
h3 { font-size: 11pt; } .meta { color: #555; margin-bottom: 1rem; }
table { border-collapse: collapse; width: 100%; margin: .4rem 0; font-size: 8.5pt; }
th, td { border: 1px solid #ccc; padding: 2px 5px; text-align: right; }
th { background: #eef2f6; } th.block { text-align: center; }
td.label, th.label { text-align: left; } tr.group td, tr.total td { font-weight: bold; }
tr.group td { background: #f6f7f9; } tr.total td { border-top: 2px solid #555; }
td.l1 { padding-left: 1.4em; } .derived { font-style: italic; }
.notes { color: #444; font-size: 8.5pt; margin: .3rem 0 0; padding-left: 1.2em; }
.failure { border: 1px solid #b8445a; background: #fbeef0; padding: .5rem; }
table.many { table-layout: fixed; font-size: 7.5pt; } table.many th.label { width: 24%; }
table.many th { overflow-wrap: break-word; hyphens: auto; }
thead { display: table-header-group; }
tr { page-break-inside: avoid; }
svg { max-width: 100%; height: auto; }
"""


MAX_COLUMNS_PER_PART = 12


def _parts(table: Table) -> list[tuple[int, int, list[tuple[str, int]] | None]]:
    """Breite Tabellen mit Spaltenblöcken werden blockweise untereinander gesetzt, damit sie
    auf eine Seite passen: (erste Spalte, Ende, Blöcke dieses Teils)."""
    if not table.header_groups or len(table.columns) <= MAX_COLUMNS_PER_PART:
        return [(0, len(table.columns), table.header_groups)]
    parts, start, pos, current = [], 0, 0, []
    for label, span in table.header_groups:
        if current and pos + span - start > MAX_COLUMNS_PER_PART:
            parts.append((start, pos, current))
            start, current = pos, []
        current.append((label, span))
        pos += span
    parts.append((start, pos, current))
    return parts


def _table_html(table: Table) -> str:
    out = []
    for start, end, groups in _parts(table):
        out += ['<table class="many">' if end - start > 8 else "<table>", "<thead>"]
        if groups:
            out.append('<tr><th class="label"></th>' + "".join(
                f'<th class="block" colspan="{span}">{escape(label)}</th>'
                for label, span in groups) + "</tr>")
        out.append('<tr><th class="label"></th>' + "".join(
            f'<th class="{"derived" if c.derived else ""}">{escape(c.label)}</th>'
            for c in table.columns[start:end]) + "</tr></thead><tbody>")
        for row in table.rows:
            cells = "".join(f"<td>{escape(_cell(row, pos))}</td>" for pos in range(start, end))
            out.append(f'<tr class="{escape(row.kind)}"><td class="label l{row.level}">'
                       f"{escape(row.label)}</td>{cells}</tr>")
        out.append("</tbody></table>")
    items = [f"<li>{escape(n['text'])}</li>" for n in table.notes]
    items += [f"<li>Quelle: {escape(s)}</li>" for s in source_lines(table)]
    if any(c.derived for c in table.columns):
        items.append("<li>Kursiv gesetzte Spalten sind berechnet, nicht gezählt.</li>")
    if items:
        out.append('<ul class="notes">' + "".join(items) + "</ul>")
    return "\n".join(out)


def to_html(report: Report) -> bytes:
    body = [f"<h1>{escape(report.title)}</h1>", f'<p class="meta">{escape(_meta(report))}</p>']
    for item in report.blocks:
        if isinstance(item, Text):
            body.append(markdown_to_html(item.markdown))
            continue
        body.append(f"<h2>{escape(item.title)}</h2>")
        if isinstance(item, Failure):
            body.append(f'<p class="failure">Fehler: {escape(item.detail)}</p>')
            continue
        chart = chart_for(item)
        if chart:
            body.append(chart)
        body.append(_table_html(item))
    if report.notes:
        body.append('<ul class="notes">' + "".join(
            f"<li>{escape(n['text'])}</li>" for n in report.notes) + "</ul>")
    html = ('<!DOCTYPE html>\n<html lang="de"><head><meta charset="utf-8">'
            f"<title>{escape(report.title)}</title><style>{_CSS}</style></head><body>\n"
            + "\n".join(body) + "\n</body></html>\n")
    return html.encode("utf-8")


class PdfUnavailable(Exception):
    """Dem System fehlen die Bibliotheken für den PDF-Satz (im Docker-Image sind sie da)."""


class ReportTooLarge(Exception):
    """Der Bericht ist zu groß für den PDF-Satz."""


MAX_PDF_CELLS = 30_000  # darüber braucht der Satz Minuten und Gigabytes


def pdf_available() -> bool:
    try:
        import weasyprint  # noqa: F401
    except (ImportError, OSError):
        return False
    return True


def to_pdf(report: Report) -> bytes:
    cells = sum(len(t.rows) * (len(t.columns) + 1) for t in report.tables)
    if cells > MAX_PDF_CELLS:
        raise ReportTooLarge
    try:
        from weasyprint import HTML  # erst hier: der Import ist schwer
        from weasyprint.urls import URLFetcher, URLFetchingError
    except (ImportError, OSError):
        raise PdfUnavailable from None

    class NoFetch(URLFetcher):
        """Der Bericht ist in sich vollständig; nichts wird aus Netz oder Dateien geladen."""

        def fetch(self, url: str, *args: Any, **kwargs: Any) -> Any:
            raise URLFetchingError("Der Bericht lädt nichts nach.")

    return HTML(string=to_html(report).decode("utf-8"),
                url_fetcher=NoFetch(allowed_protocols=[])).write_pdf()


def render(report: Report, fmt: str) -> bytes:
    if fmt == "json":
        return to_json(report)
    if fmt == "csv":
        return to_csv(report)
    if fmt == "md":
        return to_markdown(report)
    if fmt == "html":
        return to_html(report)
    if fmt == "pdf":
        return to_pdf(report)
    raise ValueError(fmt)
