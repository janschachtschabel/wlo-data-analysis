"""Läufe: eine Definition ausführen und gespeicherte Ergebnis-Tabellen lesen.

Ein Lauf enthält nur Ergebnisse der Abfrage-Engine (Zählungen), nie Datensätze.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictStr

router = APIRouter()


class RunIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    refresh: StrictBool = False
    instance: Annotated[StrictStr, Field(min_length=1, max_length=64)] | None = None
    save: StrictBool = True


def _runner(request: Request):
    return request.app.state.services.runner


@router.post("/definitions/{def_id}/run")
async def run_definition(def_id: str, request: Request, body: RunIn | None = None
                         ) -> dict[str, Any]:
    body = body or RunIn()
    return await _runner(request).run(
        def_id, refresh=body.refresh, instance=body.instance, save=body.save)


@router.get("/definitions/{def_id}/runs")
async def runs_of_definition(
    def_id: str, request: Request,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    before: Annotated[int | None, Query(ge=1)] = None,
) -> list[dict[str, Any]]:
    await request.app.state.services.definitions.get(def_id)  # 404 bei unbekannter ID
    return await _runner(request).runs(def_id, limit=limit, before=before)


@router.get("/runs")
async def all_runs(
    request: Request,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    before: Annotated[int | None, Query(ge=1)] = None,
    definition: Annotated[str | None, Query(max_length=64)] = None,
) -> list[dict[str, Any]]:
    """``definition=<id>`` nur Läufe dieser Definition (auch einer gelöschten nicht mehr:
    deren Läufe liefert ``definition=none``). Weniger Einträge als ``limit`` heißt: Ende."""
    wanted = "" if definition == "none" else definition
    return await _runner(request).runs(wanted, limit=limit, before=before)


@router.get("/runs/{run_id}")
async def get_run(run_id: str, request: Request) -> dict[str, Any]:
    return await _runner(request).get_run(run_id)


@router.delete("/runs/{run_id}", status_code=204)
async def delete_run(run_id: str, request: Request) -> Response:
    await _runner(request).delete_run(run_id)
    return Response(status_code=204)
