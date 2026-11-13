"""Abfragen im QuerySpec-Format. Liefert Tabellen aus Zählungen, nie Datensätze."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import JSONResponse

from ..engine.engine import run_query
from ..engine.spec import QuerySpec
from ..errors import QueryError, UnknownInstance

router = APIRouter()


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(QueryError)
    async def query_error(request: Request, exc: QueryError) -> JSONResponse:
        return JSONResponse({"detail": str(exc), "kind": "invalid_query"}, status_code=422)


@router.post("/query")
async def query(spec: QuerySpec, request: Request) -> dict[str, Any]:
    services = request.app.state.services
    try:
        client, vocab = services.client(spec.instance), services.vocab(spec.instance)
    except KeyError:
        raise UnknownInstance(spec.instance) from None
    return await run_query(spec, client, vocab)
