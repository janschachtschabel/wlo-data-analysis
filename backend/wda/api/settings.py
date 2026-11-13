from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from .. import __version__
from ..report.formats import FORMATS, pdf_available

router = APIRouter(prefix="/settings")


@router.get("")
def read_settings(request: Request) -> dict[str, Any]:
    settings = request.app.state.settings
    return {
        "version": __version__,
        "export": {"formats": list(FORMATS), "pdf_available": pdf_available()},
        "llm": {
            "base_url": settings.llm_base_url,
            "model": settings.llm_model,
            "api_key_set": bool(
                settings.llm_api_key and settings.llm_api_key.get_secret_value()
            ),
        },
    }
