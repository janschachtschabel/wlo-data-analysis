"""Export eines gespeicherten Laufs als JSON, CSV, Markdown, HTML oder PDF.

Die Ausgabe entsteht nur aus dem Lauf: Zählungen, Definitionen und deren Texte.
"""

from __future__ import annotations

import asyncio
import logging
import re
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from typing import Literal
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request, Response

from ..errors import QueryError
from ..report.formats import FORMATS, MAX_PDF_CELLS, PdfUnavailable, ReportTooLarge, render
from ..report.model import Report
from ..store.runner import DamagedRun
from .report import Compare, built_report

router = APIRouter()
log = logging.getLogger(__name__)

# Der PDF-Satz ist langsam und speicherhungrig: einer zur Zeit in einem eigenen Thread, damit
# er weder sich selbst vervielfacht noch den Thread-Pool der Datenbankzugriffe belegt.
_PDF_POOL = ThreadPoolExecutor(max_workers=1, thread_name_prefix="wda-pdf")
MAX_PDF_WAITING = 3
_pdf_waiting = 0


async def _render(report: Report, fmt: str) -> bytes:
    global _pdf_waiting
    if fmt != "pdf":
        return await asyncio.to_thread(render, report, fmt)
    if _pdf_waiting >= MAX_PDF_WAITING:
        raise HTTPException(
            503, "Es werden gerade mehrere PDF-Berichte gesetzt. Bitte gleich noch einmal "
                 "versuchen.", headers={"Retry-After": "20"})
    _pdf_waiting += 1
    try:
        return await asyncio.get_running_loop().run_in_executor(
            _PDF_POOL, render, report, fmt)
    finally:
        _pdf_waiting -= 1


def file_name(title: str, started_at: str, extension: str) -> str:
    plain = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^A-Za-z0-9]+", "-", plain).strip("-")[:60] or "bericht"
    day = started_at[:10] if re.fullmatch(r"\d{4}-\d{2}-\d{2}", started_at[:10]) else "lauf"
    return f"{slug}-{day}.{extension}"


@router.get("/runs/{run_id}/export")
async def export_run(
    run_id: str, request: Request,
    format: Literal["json", "csv", "md", "html", "pdf"] = "html",
    download: bool = True,
    compare: Compare = None,
) -> Response:
    """``compare`` wie beim Bericht: Die Veränderung steht dann hinter jeder Zahl, in CSV und
    JSON stehen die Vorwerte daneben."""
    run = await request.app.state.services.runner.get_run(run_id)
    try:
        report = await built_report(request, run, compare)
        body = await _render(report, format)
    except QueryError as exc:
        raise HTTPException(422, str(exc)) from None
    except ReportTooLarge:
        raise HTTPException(
            413, f"Der Bericht ist für den PDF-Export zu groß (mehr als "
                 f"{MAX_PDF_CELLS} Tabellenzellen). HTML und CSV gehen in jeder "
                 "Größe.") from None
    except (KeyError, TypeError, ValueError, IndexError, AttributeError, OverflowError):
        # mit Stacktrace: ein Fehler im Renderer sähe sonst aus wie kaputte Daten
        log.exception("Export des Laufs %s als %s gescheitert", run_id, format)
        raise DamagedRun(
            "Der gespeicherte Lauf ist beschädigt oder hat ein unerwartetes Format und "
            "lässt sich nicht darstellen. Bitte die Definition neu ausführen.") from None
    except PdfUnavailable:
        raise HTTPException(
            503, "Der PDF-Export braucht die Bibliothek Pango, die auf diesem System fehlt. "
                 "Im Docker-Image ist sie enthalten; HTML lässt sich im Browser als PDF "
                 "drucken.") from None
    media_type, extension = FORMATS[format]
    name = file_name(report.title, report.started_at, extension)
    headers = {
        "Content-Disposition": f"{'attachment' if download else 'inline'}; "
                               f"filename=\"{name}\"; filename*=UTF-8''{quote(name)}",
        # Ein exportierter Bericht führt nichts aus und lädt nichts nach.
        "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; sandbox",
    }
    return Response(body, media_type=media_type, headers=headers)
