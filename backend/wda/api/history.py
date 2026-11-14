"""Zeitreihen einer Definition aus ihren gespeicherten Läufen (nur Zählungen)."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

from ..errors import QueryError, error_body
from ..store.history import History

router = APIRouter()
Name = Annotated[str, Query(max_length=2100)]
Instance = Annotated[str | None, Query(min_length=1, max_length=64)]


def _history(request: Request) -> History:
    return request.app.state.services.history


@router.get("/definitions/{def_id}/history/keys")
async def history_keys(def_id: str, request: Request, instance: Instance = None
                       ) -> dict[str, Any]:
    """Was sich für eine Zeitreihe auswählen lässt: Blöcke, Zeilen, Spalten des jüngsten
    Laufs."""
    return await _history(request).keys(def_id, instance)


@router.get("/definitions/{def_id}/history")
async def history(
    def_id: str, request: Request,
    key: Annotated[str | None, Query(max_length=100)] = None,
    row: Annotated[list[Name] | None, Query(max_length=8)] = None,
    column: Annotated[list[Name] | None, Query(max_length=8)] = None,
    instance: Instance = None,
    start: Annotated[str | None, Query(alias="from", max_length=40)] = None,
    end: Annotated[str | None, Query(alias="to", max_length=40)] = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 200,
) -> Any:
    """Werte gewählter Zellen über die Läufe, ältester zuerst. Ohne Auswahl die Gesamtzeile
    des ersten Blocks. Läufe gegen eine andere Instanz sind eine eigene Reihe (``instance``)."""
    try:
        return await _history(request).series(
            def_id, key=key, rows=row, columns=column, instance=instance, start=start,
            end=end, limit=limit)
    except QueryError as exc:
        return JSONResponse(error_body("invalid_request", str(exc)), status_code=422)
