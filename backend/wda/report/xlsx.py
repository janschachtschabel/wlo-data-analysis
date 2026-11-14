"""Excel-Datei (XLSX) aus dem Berichtsmodell, ohne Fremdbibliothek: je Tabelle ein Blatt,
Zahlen als Zahlen. Texte stehen als Text in der Zelle, nie als Formel."""

from __future__ import annotations

import io
import re
import zipfile
from typing import Any
from xml.sax.saxutils import escape

from .model import Failure, Report, Table, Text

# in XML verboten: Steuerzeichen, einzelne Surrogate, U+FFFE und U+FFFF
_BAD = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]")
_SHEET = re.compile(r"[\[\]:*?/\\]")
MAX_ROWS = 1_000_000


def _column(index: int) -> str:
    name = ""
    index += 1
    while index:
        index, rest = divmod(index - 1, 26)
        name = chr(65 + rest) + name
    return name


def _cell(ref: str, value: Any, style: int = 0) -> str:
    look = f' s="{style}"' if style else ""
    if isinstance(value, bool) or value is None:
        return ""
    if isinstance(value, int | float):
        if value != value or value in (float("inf"), float("-inf")):
            return ""
        return f'<c r="{ref}"{look}><v>{value!r}</v></c>'
    text = escape(_BAD.sub("", str(value))[:32000])
    return f'<c r="{ref}"{look} t="inlineStr"><is><t xml:space="preserve">{text}</t></is></c>'


def _sheet(lines: list[tuple[list[Any], int]]) -> str:
    rows = []
    for number, (line, style) in enumerate(lines[:MAX_ROWS], 1):
        cells = "".join(_cell(f"{_column(n)}{number}", value, style)
                        for n, value in enumerate(line))
        rows.append(f'<row r="{number}">{cells}</row>')
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            '<sheetViews><sheetView workbookViewId="0"/></sheetViews>'
            '<cols><col min="1" max="1" width="38" customWidth="1"/>'
            '<col min="2" max="60" width="16" customWidth="1"/></cols>'
            f'<sheetData>{"".join(rows)}</sheetData></worksheet>')


def _table_lines(table: Table, with_previous: bool) -> list[tuple[list[Any], int]]:
    lines: list[tuple[list[Any], int]] = [([table.title], 1), ([], 0)]
    with_percent = any(r.percent for r in table.rows)
    header: list[Any] = [""]
    # Spaltenblöcke (etwa je Quelle) stehen vor dem Namen, sonst gäbe es gleichnamige Spalten
    blocks = [label for label, span in table.header_groups or [] for _ in range(span)]
    for pos, column in enumerate(table.columns):
        label = column.label
        if pos < len(blocks) and blocks[pos]:
            label = f"{blocks[pos]} · {label}"
        header.append(label)
        if with_percent:
            header.append(label + " (%)")
        if with_previous:
            header.append(label + " (vorher)")
    lines.append((header, 1))
    for row in table.rows:
        line: list[Any] = [("  " * row.level) + row.label]
        for pos in range(len(table.columns)):
            line.append(row.values[pos] if pos < len(row.values) else None)
            if with_percent:
                line.append(row.percent[pos] if row.percent and pos < len(row.percent) else None)
            if with_previous:
                line.append(row.previous[pos]
                            if row.previous and pos < len(row.previous) else None)
        lines.append((line, 1 if row.kind in ("total", "group") else 0))
    topics = [(row.label, column.label, topic) for row in table.rows
              for pos, column in enumerate(table.columns)
              for topic in ((row.topics or [])[pos] if row.topics and pos < len(row.topics)
                            else []) if isinstance(topic, dict)]
    if topics:
        lines += [([], 0), (["Themen je Zelle"], 1),
                  (["Zeile", "Spalte", "Thema", "Materialien", "Anteil der Zelle (%)"], 1)]
        lines += [([row, column, topic.get("label"), topic.get("count"), topic.get("share")], 0)
                  for row, column, topic in topics]
    if table.notes:
        lines.append(([], 0))
        lines += [(["Hinweis: " + str(note.get("text", ""))], 0) for note in table.notes]
    return lines


def to_xlsx(report: Report, meta: str, source_lines: Any) -> bytes:
    sheets: list[tuple[str, str]] = []
    used: set[str] = {"übersicht", "history"}  # das erste Blatt; „History“ sperrt Excel

    def name(title: str) -> str:
        base = _SHEET.sub(" ", _BAD.sub("", title)).strip(" '")[:28].strip(" '") or "Tabelle"
        candidate, n = base, 1
        while candidate.casefold() in used:
            n += 1
            candidate = base[:25].strip(" '") + f" {n}"
        used.add(candidate.casefold())
        return candidate

    overview: list[tuple[list[Any], int]] = [([report.title], 1), ([meta], 0), ([], 0),
                                            (["Blatt", "Inhalt"], 1)]
    for item in report.blocks:
        if isinstance(item, Text):
            continue
        sheet = name(item.title)
        if isinstance(item, Failure):
            overview.append(([sheet, "Fehler: " + item.detail], 0))
            sheets.append((sheet, _sheet([([item.title], 1), (["Fehler: " + item.detail], 0)])))
            continue
        lines = _table_lines(item, report.compare is not None)
        lines += [(["Quelle: " + line], 0) for line in source_lines(item)]
        overview.append(([sheet, item.title], 0))
        sheets.append((sheet, _sheet(lines)))
    overview += [(["Hinweis: " + str(note.get("text", ""))], 0) for note in report.notes]
    sheets.insert(0, ("Übersicht", _sheet(overview)))

    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as book:
        book.writestr("[Content_Types].xml", (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.'
            'relationships+xml"/><Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-'
            'officedocument.spreadsheetml.sheet.main+xml"/>'
            '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-'
            'officedocument.spreadsheetml.styles+xml"/>'
            + "".join(f'<Override PartName="/xl/worksheets/sheet{n}.xml" ContentType="'
                      'application/vnd.openxmlformats-officedocument.spreadsheetml.'
                      'worksheet+xml"/>' for n in range(1, len(sheets) + 1)) + "</Types>"))
        book.writestr("_rels/.rels", (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
            'relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>'))
        book.writestr("xl/workbook.xml", (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            "<sheets>" + "".join(
                f'<sheet name="{escape(title, {chr(34): "&quot;"})}" sheetId="{n}" r:id="rId{n}"/>'
                for n, (title, _) in enumerate(sheets, 1)) + "</sheets></workbook>"))
        book.writestr("xl/_rels/workbook.xml.rels", (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            + "".join(f'<Relationship Id="rId{n}" Type="http://schemas.openxmlformats.org/'
                      'officeDocument/2006/relationships/worksheet" '
                      f'Target="worksheets/sheet{n}.xml"/>' for n in range(1, len(sheets) + 1))
            + f'<Relationship Id="rId{len(sheets) + 1}" Type="http://schemas.openxmlformats.org/'
            'officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>'))
        book.writestr("xl/styles.xml", (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            '<fonts count="2"><font><sz val="11"/><name val="Calibri"/></font>'
            '<font><b/><sz val="11"/><name val="Calibri"/></font></fonts>'
            '<fills count="2"><fill><patternFill patternType="none"/></fill>'
            '<fill><patternFill patternType="gray125"/></fill></fills>'
            '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border>'
            '</borders><cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" '
            'borderId="0"/></cellStyleXfs><cellXfs count="2">'
            '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
            '<xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1"/>'
            '</cellXfs><cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/>'
            "</cellStyles></styleSheet>"))
        for n, (_, xml) in enumerate(sheets, 1):
            book.writestr(f"xl/worksheets/sheet{n}.xml", xml)
    return out.getvalue()
