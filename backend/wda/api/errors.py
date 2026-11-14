"""Einheitliche Fehlerantworten: immer ``{"detail": Satz, "kind": Art, …}``."""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from ..config import ConfigError
from ..errors import UnknownInstance, describe_validation, error_body
from ..store.definitions import Conflict, NotFound
from ..store.runner import DamagedRun
from ..store.specs import InvalidDefinition

_HTTP_KINDS = {401: "unauthenticated", 404: "not_found", 405: "method_not_allowed",
               409: "conflict", 413: "too_large", 429: "throttled", 503: "unavailable"}


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, exc: RequestValidationError) -> JSONResponse:
        # Ohne die Eingabe zu wiederholen (sie kann ein Passwort sein).
        return JSONResponse(
            error_body("invalid_request", describe_validation(exc.errors())), status_code=422)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        detail = exc.detail if isinstance(exc.detail, str) else "Fehler"
        return JSONResponse(
            error_body(_HTTP_KINDS.get(exc.status_code, "http_error"), detail),
            status_code=exc.status_code, headers=getattr(exc, "headers", None))

    @app.exception_handler(NotFound)
    async def not_found(request: Request, exc: NotFound) -> JSONResponse:
        return JSONResponse(error_body("not_found", str(exc)), status_code=404)

    @app.exception_handler(Conflict)
    async def conflict(request: Request, exc: Conflict) -> JSONResponse:
        return JSONResponse(error_body("conflict", str(exc), used_by=exc.used_by),
                            status_code=409)

    @app.exception_handler(InvalidDefinition)
    async def invalid_definition(request: Request, exc: InvalidDefinition) -> JSONResponse:
        return JSONResponse(error_body("invalid_definition", str(exc)), status_code=422)

    @app.exception_handler(UnknownInstance)
    async def unknown_instance(request: Request, exc: UnknownInstance) -> JSONResponse:
        return JSONResponse(error_body("unknown_instance", str(exc)), status_code=404)

    @app.exception_handler(DamagedRun)
    async def damaged_run(request: Request, exc: DamagedRun) -> JSONResponse:
        return JSONResponse(error_body("damaged_run", str(exc)), status_code=422)

    @app.exception_handler(ConfigError)
    async def config_error(request: Request, exc: ConfigError) -> JSONResponse:
        return JSONResponse(error_body("storage_unavailable", str(exc)), status_code=503)
