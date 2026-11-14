"""Ein Dokument als Vorlage für den Assistenten: nur umwandeln, nichts speichern.

PDF, Word (docx) und Textdateien werden im Speicher zu reinem Text. Der Text geht mit der
nächsten Frage an das Sprachmodell und wird danach verworfen."""

from __future__ import annotations

import io
import re
import zipfile
from typing import Any
from xml.etree import ElementTree  # noqa: S405 - defusedxml fehlt; Entities sind unten gesperrt

MAX_BYTES = 8 * 1024 * 1024
MAX_TEXT = 24_000          # Zeichen, die an das Modell gehen
MAX_PAGES = 60
MAX_UNPACKED = 30 * 1024 * 1024
TEXT_TYPES = ("txt", "md", "csv", "tsv", "json")
KINDS = ("pdf", "docx", *TEXT_TYPES)
_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_BLANK = re.compile(r"[ \t\f\v]+")
_BREAKS = re.compile(r"\n{3,}")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


class DocumentError(Exception):
    """Die Datei lässt sich nicht lesen; die Meldung ist für den Nutzer."""


def kind_of(name: str, data: bytes) -> str:
    """Art der Datei aus dem Inhalt; die Endung zählt nur bei Text."""
    if data[:5] == b"%PDF-":
        return "pdf"
    if data[:4] == b"PK\x03\x04":
        return "docx"
    ending = name.rsplit(".", 1)[-1].casefold() if "." in name else ""
    if ending in TEXT_TYPES:
        return ending
    raise DocumentError("Gelesen werden PDF, Word (docx) und Textdateien (txt, md, csv, json).")


def _pdf(data: bytes) -> tuple[str, int, bool]:
    try:
        from pypdf import PdfReader
        from pypdf.errors import PyPdfError
    except ImportError:
        raise DocumentError("PDF-Dateien kann dieser Server nicht lesen (pypdf fehlt).") from None
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            raise DocumentError("Das PDF ist verschlüsselt.")
        pages = len(reader.pages)
        parts, size = [], 0
        for page in reader.pages[:MAX_PAGES]:
            text = page.extract_text() or ""
            parts.append(text)
            size += len(text)
            if size > MAX_TEXT * 2:
                break
    except DocumentError:
        raise
    except (PyPdfError, ValueError, KeyError, TypeError, IndexError, RecursionError,
            OSError, AttributeError):
        raise DocumentError("Das PDF lässt sich nicht lesen.") from None
    return "\n\n".join(parts), pages, pages > len(parts)


def _docx(data: bytes) -> str:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as book:
            info = book.getinfo("word/document.xml")
            if info.file_size > MAX_UNPACKED:
                raise DocumentError("Das Dokument ist zu groß.")
            raw = book.read(info)
    except KeyError:
        raise DocumentError("Gelesen werden Word-Dateien im Format docx (keine Tabellen- "
                            "oder Präsentationsdateien).") from None
    except (zipfile.BadZipFile, OSError, RuntimeError, NotImplementedError):
        raise DocumentError("Die Word-Datei lässt sich nicht lesen.") from None
    if b"<!DOCTYPE" in raw[:4096] or b"<!ENTITY" in raw:
        raise DocumentError("Die Word-Datei lässt sich nicht lesen.")
    try:
        root = ElementTree.fromstring(raw)  # noqa: S314 - DOCTYPE und ENTITY oben abgewiesen
    except ElementTree.ParseError:
        raise DocumentError("Die Word-Datei lässt sich nicht lesen.") from None
    lines = []
    for paragraph in root.iter(_W + "p"):
        parts = []
        for node in paragraph.iter():
            if node.tag == _W + "t":
                parts.append(node.text or "")
            elif node.tag in (_W + "tab", _W + "br"):
                parts.append("\t" if node.tag == _W + "tab" else "\n")
        lines.append("".join(parts))
    return "\n".join(lines)


def extract(name: str, data: bytes) -> dict[str, Any]:
    """Reiner Text einer Datei, gekürzt auf das, was ein Modell als Vorlage braucht."""
    if not data:
        raise DocumentError("Die Datei ist leer.")
    if len(data) > MAX_BYTES:
        raise DocumentError(f"Die Datei ist größer als {MAX_BYTES // (1024 * 1024)} MB.")
    kind = kind_of(name, data)
    pages: int | None = None
    cut = False
    if kind == "pdf":
        text, pages, cut = _pdf(data)
    elif kind == "docx":
        text = _docx(data)
    else:
        text = data.decode("utf-8-sig", errors="replace")
    text = _CONTROL.sub("", text.replace("\r\n", "\n").replace("\r", "\n"))
    text = _BREAKS.sub("\n\n", "\n".join(_BLANK.sub(" ", line).strip()
                                         for line in text.split("\n"))).strip()
    if not text:
        raise DocumentError("In der Datei steht kein lesbarer Text (bei einem gescannten PDF "
                            "sind es nur Bilder).")
    total = len(text)
    if total > MAX_TEXT:
        text, cut = text[:MAX_TEXT].rstrip(), True
    return {"name": name, "kind": kind, "chars": total, "pages": pages, "truncated": cut,
            "text": text}
