"""Einstellungen: was die App über sich sagt, und das Sprachmodell. Der Schlüssel des
Sprachmodells lässt sich nur setzen, nie lesen."""

from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from .. import __version__
from ..errors import error_body
from ..llm.client import LlmError
from ..llm.config import LlmConfig, LlmIn, host_of
from ..report.formats import FORMATS, pdf_available

router = APIRouter(prefix="/settings")


@router.get("")
def read_settings(request: Request) -> dict[str, Any]:
    settings = request.app.state.settings
    return {
        "version": __version__,
        "timezone": settings.timezone,
        "export": {"formats": list(FORMATS), "pdf_available": pdf_available()},
        "llm": request.app.state.services.llm.public(),
    }


def _merged(body: LlmIn, current: LlmConfig) -> LlmConfig | JSONResponse:
    """Die neuen Einstellungen. Ohne neuen Schlüssel bleibt der bisherige, aber nur für
    dieselbe Adresse: Ein Schlüssel geht nie an einen Rechner, für den er nicht eingegeben
    wurde."""
    key = body.api_key.get_secret_value() if body.api_key else None
    if key is None and current.api_key:
        if host_of(body.base_url) != host_of(current.base_url):
            return JSONResponse(error_body(
                "invalid_request",
                "Ungültig: 'api_key' – für eine andere Adresse muss der Schlüssel neu "
                "eingegeben werden."), status_code=422)
        key = current.api_key
    return LlmConfig(body.base_url, body.model, key, body.effort, True)


@router.put("/llm")
async def write_llm(body: LlmIn, request: Request) -> Any:
    services = request.app.state.services
    config = _merged(body, services.llm)
    if isinstance(config, JSONResponse):
        return config
    await services.save_llm(config)
    return services.llm.public()


@router.delete("/llm")
async def reset_llm(request: Request) -> dict[str, Any]:
    """Zurück auf die Werte der Umgebung (``WDA_LLM_*``)."""
    services = request.app.state.services
    await services.save_llm(None)
    return services.llm.public()


@router.post("/llm/test")
async def test_llm(request: Request, body: LlmIn | None = None) -> Any:
    """Ein kurzer Aufruf mit den gespeicherten oder den mitgeschickten (noch nicht
    gespeicherten) Einstellungen."""
    services = request.app.state.services
    config = services.llm
    if body is not None:
        config = _merged(body, config)
        if isinstance(config, JSONResponse):
            return config
    started = time.monotonic()
    try:
        answer = await services.llm_client(config).chat(
            [{"role": "user", "content": "Antworte nur mit dem Wort: bereit"}])
    except LlmError as exc:
        return {"ok": False, "kind": exc.kind, "detail": exc.message}
    return {"ok": True, "model": config.model, "seconds": round(time.monotonic() - started, 1),
            "answer": " ".join((answer["content"] or "").split())[:80]}
