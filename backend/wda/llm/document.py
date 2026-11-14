"""Ein Dokument als Vorlage für den Assistenten: nur umwandeln, nichts speichern.

PDF, Word, Excel, PowerPoint und Textdateien werden im Speicher zu Markdown, damit das
Sprachmodell den Aufbau sieht: Überschriften, Listen und Tabellen. Der Text geht mit der
nächsten Frage an das Modell und wird danach verworfen."""

from __future__ import annotations

import io
import logging
import multiprocessing
import re
import zipfile
from collections import Counter
from typing import Any
from xml.etree import ElementTree  # noqa: S405 - gelesen wird nur über _parse
from xml.parsers import expat  # noqa: S406 - DOCTYPE und Entities brechen in _parse ab

MAX_BYTES = 8 * 1024 * 1024
MAX_TEXT = 32_000          # Zeichen, die an das Modell gehen
MAX_PAGES = 60
MAX_UNPACKED = 12 * 1024 * 1024
MAX_SECONDS = 20           # so lange darf eine Umwandlung dauern
MAX_MEMORY = 1536 * 1024 * 1024
MAX_MARKS = 4000           # Linien und Flächen je PDF-Seite, bis zu denen Tabellen gesucht werden
MAX_ROWS = 200             # Zeilen je Tabelle
MAX_COLUMNS = 30
MAX_SHEETS = 12
TEXT_TYPES = ("txt", "md", "csv", "tsv", "json")
KINDS = ("pdf", "docx", "xlsx", "pptx", *TEXT_TYPES)
_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_S = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
_P = "{http://schemas.openxmlformats.org/presentationml/2006/main}"
_R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
_REL = "{http://schemas.openxmlformats.org/package/2006/relationships}"
_BLANK = re.compile(r"[ \t\f\v]+")
_BREAKS = re.compile(r"\n{3,}")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_HEADING = re.compile(r"(?:heading|berschrift)\s*([1-9])", re.IGNORECASE)
_BULLET = re.compile(r"^[•●▪■◦○‣⁃∙·*–-]\s+")
_NUMBER = re.compile(r"\D+")
_UNREADABLE = "Die Datei lässt sich nicht lesen."


class DocumentError(Exception):
    """Die Datei lässt sich nicht lesen; die Meldung ist für den Nutzer."""


# --- Markdown-Bausteine ---------------------------------------------------------------

def _cell(text: str) -> str:
    return " ".join(text.replace("|", "\\|").split())


def _table(rows: list[list[str]]) -> str:
    """Eine Markdown-Tabelle; die erste Zeile ist der Kopf. Leere Ränder fallen weg."""
    rows = [[_cell(c or "") for c in row[:MAX_COLUMNS]] for row in rows[:MAX_ROWS]]
    rows = [row for row in rows if any(row)]
    width = max((len(row) for row in rows), default=0)
    used = [i for i in range(width) if any(i < len(row) and row[i] for row in rows)]
    if not rows or not used:
        return ""
    lines = ["| " + " | ".join(row[i] if i < len(row) else "" for i in used) + " |"
             for row in rows]
    lines.insert(1, "|" + " --- |" * len(used))
    return "\n".join(lines)


# --- Office-Dateien (zip mit XML) -----------------------------------------------------

class _Book:
    """Liest XML-Teile einer Office-Datei mit einer Grenze für die entpackte Menge."""

    def __init__(self, book: zipfile.ZipFile) -> None:
        self.book = book
        self.names = set(book.namelist())
        self.spent = 0

    def xml(self, name: str) -> ElementTree.Element | None:
        if name not in self.names:
            return None
        info = self.book.getinfo(name)
        self.spent += info.file_size
        if self.spent > MAX_UNPACKED:
            raise DocumentError("Das Dokument ist zu groß.")
        return _parse(self.book.read(info))


def _refuse(*_: Any) -> None:
    raise DocumentError(_UNREADABLE)


def _parse(raw: bytes) -> ElementTree.Element:
    """XML ohne DOCTYPE und ohne Entities, gleich in welcher Kodierung sie stehen."""
    builder = ElementTree.TreeBuilder()
    parser = expat.ParserCreate(namespace_separator="}")
    parser.buffer_text = True

    def name(raw: str) -> str:
        return "{" + raw if "}" in raw else raw

    parser.StartDoctypeDeclHandler = _refuse
    parser.EntityDeclHandler = _refuse
    parser.StartElementHandler = lambda tag, attrs: builder.start(
        name(tag), {name(key): value for key, value in attrs.items()})
    parser.EndElementHandler = lambda tag: builder.end(name(tag))
    parser.CharacterDataHandler = builder.data
    try:
        parser.Parse(raw, True)
        return builder.close()
    except (expat.ExpatError, AssertionError):
        raise DocumentError(_UNREADABLE) from None


def _on(node: ElementTree.Element | None) -> bool:
    return node is not None and node.get(_W + "val", "true") not in ("0", "false", "off")


def _runs(paragraph: ElementTree.Element, marks: bool) -> str:
    """Text eines Absatzes; fett Gesetztes bleibt als ``**…**`` erkennbar."""
    parts: list[tuple[str, bool]] = []
    for run in paragraph.iter(_W + "r"):
        props = run.find(_W + "rPr")
        bold = marks and props is not None and _on(props.find(_W + "b"))
        text = "".join(
            (node.text or "") if node.tag == _W + "t"
            else "\t" if node.tag == _W + "tab" else " " if node.tag == _W + "br" else ""
            for node in run)
        if not text:
            continue
        if parts and parts[-1][1] == bold:
            parts[-1] = (parts[-1][0] + text, bold)
        else:
            parts.append((text, bold))
    if len(parts) == 1 and parts[0][1]:
        return "**" + parts[0][0].strip() + "**" if parts[0][0].strip() else ""
    out = []
    for text, bold in parts:
        core = text.strip()
        if bold and core:
            lead, tail = text[:len(text) - len(text.lstrip())], text[len(text.rstrip()):]
            out.append(f"{lead}**{core}**{tail}")
        else:
            out.append(text)
    return "".join(out)


def _numbering(book: _Book) -> dict[tuple[str, str], bool]:
    """(numId, Ebene) → nummeriert? aus ``numbering.xml``."""
    root = book.xml("word/numbering.xml")
    if root is None:
        return {}
    abstract: dict[str, dict[str, bool]] = {}
    for node in root.iter(_W + "abstractNum"):
        levels = {}
        for level in node.iter(_W + "lvl"):
            kind = level.find(_W + "numFmt")
            levels[level.get(_W + "ilvl", "0")] = (
                kind is not None and kind.get(_W + "val") not in ("bullet", "none"))
        abstract[node.get(_W + "abstractNumId", "")] = levels
    out = {}
    for node in root.iter(_W + "num"):
        ref = node.find(_W + "abstractNumId")
        for level, ordered in abstract.get(ref.get(_W + "val", "") if ref is not None else "",
                                           {}).items():
            out[(node.get(_W + "numId", ""), level)] = ordered
    return out


def _blocks(parent: ElementTree.Element):
    """Absätze und Tabellen in der Reihenfolge des Dokuments, auch in Inhaltssteuerelementen."""
    for node in parent:
        if node.tag in (_W + "p", _W + "tbl"):
            yield node
        elif node.tag in (_W + "sdt", _W + "sdtContent", _W + "ins", _W + "smartTag",
                          _W + "customXml"):
            yield from _blocks(node)


def _docx(book: _Book) -> str:
    root = book.xml("word/document.xml")
    body = root.find(_W + "body") if root is not None else None
    if body is None:
        raise DocumentError(_UNREADABLE)
    ordered = _numbering(book)
    out: list[str] = []
    in_list = False
    for block in _blocks(body):
        if block.tag == _W + "tbl":
            rows = [["<br>".join(filter(None, (_runs(p, False).strip()
                                               for p in cell.iter(_W + "p"))))
                     for cell in row.findall(_W + "tc")]
                    for row in block.findall(_W + "tr")[:MAX_ROWS]]
            table = _table(rows)
            if table:
                out += ["", table, ""]
            in_list = False
            continue
        props = block.find(_W + "pPr")
        style = level = number = None
        if props is not None:
            node = props.find(_W + "pStyle")
            style = node.get(_W + "val", "") if node is not None else None
            node = props.find(_W + "outlineLvl")
            level = node.get(_W + "val") if node is not None else None
            number = props.find(_W + "numPr")
        heading = 0
        if style and (found := _HEADING.search(style)):
            heading = int(found.group(1))
        elif style and style.casefold() in ("title", "titel"):
            heading = 1
        elif style and style.casefold() in ("subtitle", "untertitel"):
            heading = 2
        elif level is not None and level.isdigit() and int(level) < 9:
            heading = int(level) + 1
        text = _runs(block, marks=not heading).strip()
        if not text:
            if not in_list:
                out.append("")
            continue
        listed = number is not None or bool(
            style and re.search(r"list|aufz|liste", style, re.IGNORECASE))
        if heading:
            out += ["", "#" * min(heading, 6) + " " + " ".join(text.split()), ""]
        elif listed:
            depth = num = "0"
            if number is not None:
                node = number.find(_W + "ilvl")
                depth = node.get(_W + "val", "0") if node is not None else "0"
                node = number.find(_W + "numId")
                num = node.get(_W + "val", "0") if node is not None else "0"
            numbered = ordered.get((num, depth), bool(
                style and re.search(r"number|nummer", style, re.IGNORECASE)))
            indent = "  " * min(int(depth) if depth.isdigit() else 0, 6)
            if not in_list:
                out.append("")
            out.append(f"{indent}{'1.' if numbered else '-'} {text}")
        else:
            out += ["", text]
        in_list = listed and not heading
    return "\n".join(out)


def _column(ref: str) -> int:
    index = 0
    for char in ref:
        if not char.isalpha():
            break
        index = index * 26 + ord(char.upper()) - 64
    return index - 1


def _xlsx(book: _Book) -> tuple[str, bool]:
    workbook = book.xml("xl/workbook.xml")
    if workbook is None:
        raise DocumentError(_UNREADABLE)
    targets = {}
    rels = book.xml("xl/_rels/workbook.xml.rels")
    for rel in rels.iter(_REL + "Relationship") if rels is not None else ():
        target = rel.get("Target", "")
        targets[rel.get("Id", "")] = target.lstrip("/") if target.startswith("/") \
            else "xl/" + target
    strings: list[str] = []
    shared = book.xml("xl/sharedStrings.xml")
    for item in shared.iter(_S + "si") if shared is not None else ():
        phonetic = {id(node) for part in item.findall(_S + "rPh") for node in part.iter()}
        strings.append("".join(node.text or "" for node in item.iter(_S + "t")
                               if id(node) not in phonetic))
    out, cut = [], False
    sheets = list(workbook.iter(_S + "sheet"))
    cut = len(sheets) > MAX_SHEETS
    for sheet in sheets[:MAX_SHEETS]:
        root = book.xml(targets.get(sheet.get(_R + "id", ""), ""))
        if root is None:
            continue
        rows: list[list[str]] = []
        for row in root.iter(_S + "row"):
            if len(rows) >= MAX_ROWS:
                cut = True
                break
            line: list[str] = []
            for position, cell in enumerate(row.findall(_S + "c")):
                at = _column(cell.get("r", "")) if cell.get("r") else position
                if at < 0 or at >= MAX_COLUMNS:
                    cut = cut or at >= MAX_COLUMNS
                    continue
                kind, value = cell.get("t", "n"), cell.find(_S + "v")
                raw = value.text or "" if value is not None else ""
                if kind == "s":
                    text = strings[int(raw)] if raw.isdigit() and int(raw) < len(strings) else ""
                elif kind == "inlineStr":
                    text = "".join(node.text or "" for node in cell.iter(_S + "t"))
                elif kind == "b":
                    text = "ja" if raw == "1" else "nein"
                else:
                    text = raw
                line += [""] * (at + 1 - len(line))
                line[at] = text
            if any(line) or rows:
                rows.append(line)
        table = _table(rows)
        if table:
            out += [f"## Blatt: {' '.join(sheet.get('name', '').split())}", "", table, ""]
    return "\n".join(out), cut


def _pptx(book: _Book) -> tuple[str, int, bool]:
    names = sorted((n for n in book.names if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)),
                   key=lambda n: int(_NUMBER.sub("", n.rsplit("/", 1)[-1]) or 0))
    if not names:
        raise DocumentError(_UNREADABLE)
    out: list[str] = []
    for number, name in enumerate(names[:MAX_PAGES], start=1):
        root = book.xml(name)
        title, body = "", []
        for shape in root.iter() if root is not None else ():
            if shape.tag == _P + "sp":
                holder = shape.find(f"{_P}nvSpPr/{_P}nvPr/{_P}ph")
                is_title = holder is not None and holder.get("type") in ("title", "ctrTitle")
                for paragraph in shape.iter(_A + "p"):
                    text = " ".join("".join(
                        node.text or "" for node in paragraph.iter(_A + "t")).split())
                    if not text:
                        continue
                    if is_title and not title:
                        title = text
                        continue
                    props = paragraph.find(_A + "pPr")
                    depth = props.get("lvl", "0") if props is not None else "0"
                    body.append("  " * min(int(depth) if depth.isdigit() else 0, 6) + "- " + text)
            elif shape.tag == _A + "tbl":
                table = _table([["".join(node.text or "" for node in cell.iter(_A + "t"))
                                 for cell in row.findall(_A + "tc")]
                                for row in shape.findall(_A + "tr")])
                if table:
                    body += ["", table, ""]
        out += [f"## Folie {number}" + (f": {title}" if title else ""), "", *body, ""]
    return "\n".join(out), len(names), len(names) > MAX_PAGES


def _office(data: bytes) -> tuple[str, str, int | None, bool]:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            book = _Book(archive)
            if "word/document.xml" in book.names:
                return "docx", _docx(book), None, False
            if "xl/workbook.xml" in book.names:
                text, cut = _xlsx(book)
                return "xlsx", text, None, cut
            if "ppt/presentation.xml" in book.names:
                text, pages, cut = _pptx(book)
                return "pptx", text, pages, cut
    except DocumentError:
        raise
    except (zipfile.BadZipFile, ElementTree.ParseError):
        raise DocumentError(_UNREADABLE) from None
    raise DocumentError("Gelesen werden Word (docx), Excel (xlsx) und PowerPoint (pptx), "
                        "keine anderen gepackten Dateien.")


# --- PDF ------------------------------------------------------------------------------

def _pdf(data: bytes) -> tuple[str, int, bool]:
    """Markdown aus einem PDF: Überschriften nach Schriftgröße, Tabellen nach ihren Linien,
    Aufzählungen nach ihrem Zeichen. Ein PDF kennt keinen Aufbau; das ist eine Schätzung."""
    try:
        import pdfplumber
        from pdfminer.pdfdocument import PDFPasswordIncorrect
        from pdfminer.pdfparser import PDFSyntaxError
        from pdfplumber.utils.exceptions import PdfminerException
    except ImportError:
        raise DocumentError("PDF-Dateien kann dieser Server nicht lesen "
                            "(pdfplumber fehlt).") from None
    for noisy in ("pdfminer", "pdfplumber"):
        # pdfminer schreibt Bruchstücke des Inhalts in seine Warnungen; nichts davon ins Log
        logging.getLogger(noisy).setLevel(logging.CRITICAL)
        logging.getLogger(noisy).propagate = False
    try:
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            pages = len(pdf.pages)
            lines: list[dict[str, Any]] = []   # {page, top, bottom, text, size, bold} | table
            volume = 0
            read = 0
            for index, page in enumerate(pdf.pages[:MAX_PAGES]):
                read += 1
                boxes = []
                marks = len(page.lines) + len(page.rects)
                for table in page.find_tables()[:20] if marks <= MAX_MARKS else ():
                    rows = table.extract()
                    if len(rows) < 2 or max((len(r) for r in rows), default=0) < 2:
                        continue
                    markdown = _table([[c or "" for c in row] for row in rows])
                    if markdown:
                        boxes.append(table.bbox)
                        lines.append({"page": index, "top": table.bbox[1],
                                      "bottom": table.bbox[3], "table": markdown})
                        volume += len(markdown)

                def outside(obj: dict[str, Any], boxes: list[Any] = boxes) -> bool:
                    if obj.get("object_type") != "char":
                        return True
                    x, y = (obj["x0"] + obj["x1"]) / 2, (obj["top"] + obj["bottom"]) / 2
                    return not any(b[0] <= x <= b[2] and b[1] <= y <= b[3] for b in boxes)

                rest = page.filter(outside) if boxes else page
                for line in rest.extract_text_lines(return_chars=True):
                    chars = [c for c in line["chars"] if c.get("text", "").strip()]
                    text = " ".join(line["text"].split())
                    if not text or not chars:
                        continue
                    sizes = Counter(round(float(c.get("size") or 0), 1) for c in chars)
                    bold = sum("bold" in str(c.get("fontname", "")).casefold()
                               for c in chars) > len(chars) * 0.8
                    lines.append({"page": index, "top": line["top"], "bottom": line["bottom"],
                                  "text": text, "size": sizes.most_common(1)[0][0],
                                  "bold": bold})
                    volume += len(text)
                page.flush_cache()
                if volume > MAX_TEXT * 2:
                    break
    except DocumentError:
        raise
    except PDFPasswordIncorrect:
        raise DocumentError("Das PDF ist verschlüsselt.") from None
    except (PdfminerException, PDFSyntaxError):
        raise DocumentError("Das PDF lässt sich nicht lesen.") from None

    weight: Counter[float] = Counter()
    for line in lines:
        if "text" in line:
            weight[line["size"]] += len(line["text"])
    base = weight.most_common(1)[0][0] if weight else 0.0
    larger = sorted((size for size in weight if size >= base * 1.12 and size > base + 0.5),
                    reverse=True)[:3]
    out: list[str] = []
    before: dict[str, Any] | None = None
    for line in sorted(lines, key=lambda item: (item["page"], item["top"])):
        if "table" in line:
            out += ["", line["table"], ""]
            before = None
            continue
        text = line["text"]
        gap = before is None or line["page"] != before["page"] \
            or line["top"] - before["bottom"] > (line["bottom"] - line["top"]) * 0.7
        short = len(text) <= 120
        if line["size"] in larger and short:
            out += ["", "#" * (larger.index(line["size"]) + 1) + " " + text, ""]
        elif line["bold"] and short and gap and not text.endswith((".", ",", ";")):
            out += ["", "#" * (len(larger) + 1) + " " + text, ""]
        elif _BULLET.match(text):
            out.append("- " + _BULLET.sub("", text))
        else:
            if gap:
                out.append("")
            out.append(text)
        before = line
    return "\n".join(out), pages, pages > read


# --- Eingang --------------------------------------------------------------------------

def _delimited(text: str, separator: str) -> str:
    import csv
    try:
        rows = [row for _, row in zip(range(MAX_ROWS + 1), csv.reader(
            io.StringIO(text), delimiter=separator), strict=False)]
    except csv.Error:
        return text
    return _table(rows) or text


def _tidy(text: str) -> str:
    """Zeilenenden, Steuerzeichen und Leerraum glätten; der Einzug von Listen bleibt."""
    lines = []
    for line in _CONTROL.sub("", text.replace("\r\n", "\n").replace("\r", "\n")).split("\n"):
        line = line.rstrip().replace("\t", "  ")
        body = line.lstrip(" ")
        indent = min(len(line) - len(body), 12)
        lines.append(" " * indent + _BLANK.sub(" ", body) if body else "")
    return _BREAKS.sub("\n\n", "\n".join(lines)).strip("\n").rstrip()


def extract(name: str, data: bytes) -> dict[str, Any]:
    """Markdown einer Datei, gekürzt auf das, was ein Modell als Vorlage braucht."""
    if not data:
        raise DocumentError("Die Datei ist leer.")
    if len(data) > MAX_BYTES:
        raise DocumentError(f"Die Datei ist größer als {MAX_BYTES // (1024 * 1024)} MB.")
    pages: int | None = None
    cut = False
    ending = name.rsplit(".", 1)[-1].casefold() if "." in name else ""
    if data[:5] == b"%PDF-" or data[:4] == b"PK\x03\x04":
        kind = "pdf" if data[:1] == b"%" else "office"
        try:
            if kind == "pdf":
                text, pages, cut = _pdf(data)
            else:
                kind, text, pages, cut = _office(data)
        except DocumentError:
            raise
        except Exception:  # noqa: BLE001 - fremde Leser haben einen offenen Fehlerraum
            # bewusst ohne Protokoll: Die Meldung könnte Inhalt der Datei tragen
            raise DocumentError("Das PDF lässt sich nicht lesen." if kind == "pdf"
                                else _UNREADABLE) from None
    elif ending in TEXT_TYPES:
        kind = ending
        text = data.decode("utf-8-sig", errors="replace")
        if kind in ("csv", "tsv"):
            first = text.split("\n", 1)[0]
            separator = "\t" if kind == "tsv" else ";" if first.count(";") > first.count(",") \
                else ","
            text = _delimited(text, separator)
    else:
        raise DocumentError("Gelesen werden PDF, Word (docx), Excel (xlsx), PowerPoint (pptx) "
                            "und Textdateien (txt, md, csv, json).")
    text = _tidy(text)
    if not text.strip():
        raise DocumentError(
            "In dem PDF steht kein lesbarer Text; bei einem gescannten PDF sind es nur Bilder."
            if kind == "pdf" else "In der Datei steht kein lesbarer Text.")
    total = len(text)
    if total > MAX_TEXT:
        text, cut = text[:MAX_TEXT].rstrip(), True
    return {"name": name, "kind": kind, "chars": total, "pages": pages, "truncated": cut,
            "format": "markdown", "text": text}


def _child(pipe: Any, name: str, data: bytes) -> None:
    try:
        try:
            import resource
            resource.setrlimit(resource.RLIMIT_AS, (MAX_MEMORY, MAX_MEMORY))
        except (ImportError, ValueError, OSError):
            pass  # Windows kennt die Grenze nicht; dort schützt nur die Zeit
        pipe.send((True, extract(name, data)))
    except DocumentError as exc:
        pipe.send((False, str(exc)))
    except BaseException:  # noqa: BLE001 - auch Speichermangel wird zur schlichten Meldung
        pipe.send((False, _UNREADABLE))


def convert(name: str, data: bytes, seconds: float = MAX_SECONDS) -> dict[str, Any]:
    """Wie ``extract``, aber PDF und Office-Dateien in einem eigenen Prozess mit Zeit- und
    Speichergrenze: Eine wenige Kilobyte große Datei kann sonst den Server minutenlang binden."""
    if not data or len(data) > MAX_BYTES or data[:5] != b"%PDF-" and data[:4] != b"PK\x03\x04":
        return extract(name, data)
    context = multiprocessing.get_context("spawn")
    ours, theirs = context.Pipe(duplex=False)
    worker = context.Process(target=_child, args=(theirs, name, data), daemon=True)
    worker.start()
    theirs.close()
    try:
        if not ours.poll(seconds):
            raise DocumentError("Die Umwandlung dauert zu lange; die Datei ist zu aufwendig. "
                                "Hilft oft: nur die nötigen Seiten als eigene Datei anhängen.")
        ok, value = ours.recv()
    except (EOFError, OSError):
        raise DocumentError("Die Datei ist zu aufwendig für die Umwandlung.") from None
    finally:
        if worker.is_alive():
            worker.kill()
        worker.join(5)
        ours.close()
    if not ok:
        raise DocumentError(value)
    return value
