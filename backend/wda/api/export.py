"""Export eines gespeicherten Laufs als JSON, CSV, Markdown, HTML oder PDF.

Die Ausgabe entsteht nur aus dem Lauf: Zählungen, Definitionen und deren Texte.
"""

from __future__ import annotations

import asyncio
import re
import unicodedata
from typing import Literal
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request, Response

from ..report.build import build_report
from ..report.formats import FORMATS, MAX_PDF_CELLS, PdfUnavailable, ReportTooLarge, render
from ..store.runner import DamagedRun

router = APIRouter()


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
) -> Response:
    run = await request.app.state.services.runner.get_run(run_id)
    try:
        report = build_report(run)
        body = await asyncio.to_thread(render, report, format)  # PDF-Satz braucht Rechenzeit
    except ReportTooLarge:
        raise HTTPException(
            413, f"Der Bericht ist für den PDF-Export zu groß (mehr als "
                 f"{MAX_PDF_CELLS} Tabellenzellen). HTML und CSV gehen in jeder "
                 "Größe.") from None
    except (KeyError, TypeError, ValueError, IndexError, AttributeError, OverflowError):
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
