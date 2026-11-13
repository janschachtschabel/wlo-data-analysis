"""Kleine Markdown-Umsetzung für Texte aus Definitionen: Überschriften, Absätze, Listen,
fett und kursiv. Alles andere, auch rohes HTML, bleibt maskierter Text."""

from __future__ import annotations

import re
from html import escape

_BOLD = re.compile(r"\*\*(?=\S)(.+?)(?<=\S)\*\*")
_ITALIC = re.compile(r"(?<![*\w])\*(?=\S)([^*]+?)(?<=\S)\*(?![*\w])")
_HEADING = re.compile(r"^(#{1,4})\s+(.*)$")
_ITEM = re.compile(r"^\s*[-*]\s+(.*)$")


def inline(text: str) -> str:
    out = escape(text, quote=True)
    out = _BOLD.sub(r"<strong>\1</strong>", out)
    return _ITALIC.sub(r"<em>\1</em>", out)


def markdown_to_html(markdown: str, heading_offset: int = 1) -> str:
    """``heading_offset`` verschiebt Überschriften, damit der Berichtstitel die einzige h1
    bleibt."""
    html: list[str] = []
    paragraph: list[str] = []
    items: list[str] = []

    def flush() -> None:
        if paragraph:
            html.append("<p>" + "<br>".join(inline(line) for line in paragraph) + "</p>")
            paragraph.clear()
        if items:
            html.append("<ul>" + "".join(f"<li>{inline(i)}</li>" for i in items) + "</ul>")
            items.clear()

    for raw in markdown.replace("\r\n", "\n").split("\n"):
        line = raw.rstrip()
        if not line.strip():
            flush()
        elif match := _HEADING.match(line):
            flush()
            level = min(len(match.group(1)) + heading_offset, 6)
            html.append(f"<h{level}>{inline(match.group(2))}</h{level}>")
        elif match := _ITEM.match(line):
            if paragraph:
                flush()
            items.append(match.group(1))
        elif items and not paragraph:
            items[-1] += " " + line.strip()  # Folgezeile eines Listenpunkts
        else:
            paragraph.append(line.strip())
    flush()
    return "\n".join(html)
