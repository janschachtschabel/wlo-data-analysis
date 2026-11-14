"""Word-Datei (DOCX) aus dem Berichtsmodell, ohne Fremdbibliothek: Titel, Texte, Tabellen,
Hinweise und Quellen. Diagramme erscheinen als ihre Tabelle; breite Tabellen stehen in Teilen
untereinander, damit die Seite sie fasst."""

from __future__ import annotations

import io
import re
import zipfile
from collections.abc import Callable
from xml.sax.saxutils import escape

from .formats import _cell, _row_label
from .model import Failure, Report, Table, Text

# in XML verboten: Steuerzeichen, einzelne Surrogate, U+FFFE und U+FFFF
_BAD = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff￾￿]")
_MARKUP = re.compile(r"(\*\*|__|`)")
_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
MAX_COLUMNS = 6     # Spalten je Teil neben der Zeilenbeschriftung (Hochformat A4)
MAX_ROWS = 2000     # je Tabelle; mehr trägt ein Textdokument nicht
MAX_TOPICS = 3000
MAX_TEXT = 8000     # Zeichen je Absatz oder Zelle
_ITEM = re.compile(r"^\d{1,3}[.)] ")
_NS = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'


def _run(text: str, bold: bool = False) -> str:
    text = _BAD.sub("", text)
    clean = escape(text if len(text) <= MAX_TEXT else text[:MAX_TEXT] + " … (gekürzt)")
    look = "<w:rPr><w:b/></w:rPr>" if bold else ""
    return f'<w:r>{look}<w:t xml:space="preserve">{clean}</w:t></w:r>'


def _para(text: str, style: str | None = None, bold: bool = False) -> str:
    look = f'<w:pPr><w:pStyle w:val="{style}"/></w:pPr>' if style else ""
    return f"<w:p>{look}{_run(text, bold)}</w:p>"


def _plain(text: str) -> str:
    return _MARKUP.sub("", _LINK.sub(r"\1", text)).strip()


def _markdown(markdown: str) -> list[str]:
    """Der Text eines Blocks als Absätze: Überschriften, Aufzählungen, sonst Fließtext."""
    out, buffer = [], []

    def flush() -> None:
        if buffer:
            out.append(_para(_plain(" ".join(buffer))))
            buffer.clear()

    for raw in markdown.splitlines():
        line = raw.strip()
        if not line:
            flush()
        elif line.startswith("#"):
            flush()
            level = min(len(line) - len(line.lstrip("#")), 2) + 1
            out.append(_para(_plain(line.lstrip("#")), f"Heading{level}"))
        elif line[:2] in ("- ", "* "):
            flush()
            out.append(_para("• " + _plain(line[2:])))
        elif _ITEM.match(line) or line.startswith("|"):
            flush()  # nummerierte Punkte und Tabellenzeilen bleiben je eine Zeile
            if not set(line) <= set("|-: "):
                out.append(_para(_plain(line.strip("|").replace("|", " · "))))
        else:
            buffer.append(line)
    flush()
    return out


def _cell_xml(text: str, bold: bool, right: bool, shade: bool) -> str:
    props = ('<w:tcPr><w:shd w:val="clear" w:color="auto" w:fill="E8EEF4"/></w:tcPr>'
             if shade else "")
    align = '<w:pPr><w:jc w:val="right"/></w:pPr>' if right else ""
    return f"<w:tc>{props}<w:p>{align}{_run(text, bold)}</w:p></w:tc>"


def _grid(lines: list[tuple[list[str], bool]]) -> str:
    rows = []
    for number, (line, strong) in enumerate(lines):
        cells = "".join(_cell_xml(text, strong or number == 0, bool(pos) and number > 0,
                                  number == 0) for pos, text in enumerate(line))
        head = "<w:trPr><w:tblHeader/></w:trPr>" if number == 0 else ""
        rows.append(f"<w:tr>{head}{cells}</w:tr>")
    border = "".join(f'<w:{side} w:val="single" w:sz="4" w:space="0" w:color="9AA5B1"/>'
                     for side in ("top", "left", "bottom", "right", "insideH", "insideV"))
    width = max((len(line) for line, _ in lines), default=1)
    share = 9638 // max(width + 1, 1)  # die erste Spalte doppelt so breit
    grid = f'<w:gridCol w:w="{share * 2}"/>' + f'<w:gridCol w:w="{share}"/>' * (width - 1)
    return ('<w:tbl><w:tblPr><w:tblW w:w="5000" w:type="pct"/>'
            f"<w:tblBorders>{border}</w:tblBorders></w:tblPr><w:tblGrid>{grid}</w:tblGrid>"
            f"{''.join(rows)}</w:tbl>")


def _table(table: Table, source_lines: Callable[[Table], list[str]]) -> list[str]:
    out = [_para(table.title, "Heading2")]
    blocks = [label for label, span in table.header_groups or [] for _ in range(span)]
    rows = table.rows[:MAX_ROWS]
    count = len(table.columns)
    for start in range(0, max(count, 1), MAX_COLUMNS):
        end = min(start + MAX_COLUMNS, count)
        header = [""]
        for pos in range(start, end):
            label = table.columns[pos].label
            if pos < len(blocks) and blocks[pos]:
                label = f"{blocks[pos]} · {label}"
            header.append(label)
        lines = [(header, True)]
        for row in rows:
            cells = [_cell(row, pos) if pos < len(row.values) else "–"
                     for pos in range(start, end)]
            lines.append(([_row_label(row), *cells], row.kind in ("total", "group")))
        out.append(_grid(lines))
        if end < count:
            out.append(_para(""))
    if len(table.rows) > MAX_ROWS:
        out.append(_para(f"Hinweis: Gezeigt sind die ersten {MAX_ROWS} von "
                         f"{len(table.rows)} Zeilen; alle stehen im Excel- oder CSV-Export."))
    topics = [(row.label, column.label, topic) for row in rows
              for pos, column in enumerate(table.columns)
              for topic in ((row.topics or [])[pos] if row.topics and pos < len(row.topics)
                            else []) if isinstance(topic, dict)]
    if topics:
        out.append(_para("Themen je Zelle", bold=True))
        lines = [(["Zeile", "Spalte", "Thema", "Materialien"], True)]
        lines += [([row, column, str(topic.get("label", "")), str(topic.get("count", ""))], False)
                  for row, column, topic in topics[:MAX_TOPICS]]
        out.append(_grid(lines))
    out += [_para("Hinweis: " + str(note.get("text", "")), "Note") for note in table.notes]
    out += [_para("Quelle: " + line, "Note") for line in source_lines(table)]
    return out


_STYLES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    f"<w:styles {_NS}>"
    '<w:docDefaults><w:rPrDefault><w:rPr><w:rFonts w:ascii="Calibri" w:hAnsi="Calibri" '
    'w:cs="Calibri"/><w:sz w:val="20"/><w:lang w:val="de-DE"/></w:rPr></w:rPrDefault>'
    '<w:pPrDefault><w:pPr><w:spacing w:after="80"/></w:pPr></w:pPrDefault></w:docDefaults>'
    '<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/>'
    "</w:style>"
    + "".join(
        f'<w:style w:type="paragraph" w:styleId="{ident}"><w:name w:val="{name}"/>'
        '<w:basedOn w:val="Normal"/><w:next w:val="Normal"/>'
        f'<w:pPr><w:keepNext/><w:spacing w:before="{before}" w:after="120"/>'
        f'<w:outlineLvl w:val="{level}"/></w:pPr>'
        f'<w:rPr><w:b/><w:sz w:val="{size}"/></w:rPr></w:style>'
        for ident, name, before, level, size in (
            ("Title", "Title", 0, 0, 36), ("Heading1", "heading 1", 360, 0, 30),
            ("Heading2", "heading 2", 300, 1, 26), ("Heading3", "heading 3", 240, 2, 22)))
    + '<w:style w:type="paragraph" w:styleId="Note"><w:name w:val="Note"/>'
    '<w:basedOn w:val="Normal"/><w:rPr><w:color w:val="555555"/><w:sz w:val="18"/></w:rPr>'
    "</w:style></w:styles>")


def to_docx(report: Report, meta: str, source_lines: Callable[[Table], list[str]]) -> bytes:
    body = [_para(report.title, "Title"), _para(meta, "Note")]
    for item in report.blocks:
        if isinstance(item, Text):
            body += _markdown(item.markdown)
        elif isinstance(item, Failure):
            body += [_para(item.title, "Heading2"), _para("Fehler: " + item.detail)]
        else:
            body += _table(item, source_lines)
    body += [_para("Hinweis: " + str(note.get("text", "")), "Note") for note in report.notes]
    page = ('<w:sectPr><w:pgSz w:w="11906" w:h="16838"/><w:pgMar w:top="1134" w:right="1134" '
            'w:bottom="1134" w:left="1134" w:header="708" w:footer="708" w:gutter="0"/>'
            "</w:sectPr>")
    document = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                f"<w:document {_NS}><w:body>{''.join(body)}{page}</w:body></w:document>")
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as book:
        book.writestr("[Content_Types].xml", (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.'
            'relationships+xml"/><Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats'
            '-officedocument.wordprocessingml.document.main+xml"/>'
            '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-'
            'officedocument.wordprocessingml.styles+xml"/></Types>'))
        book.writestr("_rels/.rels", (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
            'relationships/officeDocument" Target="word/document.xml"/></Relationships>'))
        book.writestr("word/_rels/document.xml.rels", (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
            'relationships/styles" Target="styles.xml"/></Relationships>'))
        book.writestr("word/styles.xml", _STYLES)
        book.writestr("word/document.xml", document)
    return out.getvalue()
