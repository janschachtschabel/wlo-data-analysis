"""Läufe: eine Definition ausführen und gespeicherte Ergebnis-Tabellen lesen.

Ein Lauf enthält nur Ergebnisse der Abfrage-Engine (Zählungen), nie Datensätze.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictStr, model_validator

from ..errors import UnknownInstance
from .report import report_of

router = APIRouter()


class RunIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    refresh: StrictBool = False
    instance: Annotated[StrictStr, Field(min_length=1, max_length=64)] | None = None
    save: StrictBool = True
    report: StrictBool = False  # das Berichtsmodell gleich mitliefern
    # false: als Auftrag starten; Antwort 202, Stand und Ergebnis unter /runs/{id}
    wait: StrictBool = True

    @model_validator(mode="after")
    def _job_is_saved(self) -> RunIn:
        if not self.wait and (not self.save or self.report):
            raise ValueError("Ein Auftrag (wait=false) wird immer gespeichert und liefert "
                             "keinen Bericht mit")
        return self


def _runner(request: Request):
    return request.app.state.services.runner


@router.post("/definitions/{def_id}/run", response_model=None)
async def run_definition(def_id: str, request: Request, body: RunIn | None = None
                         ) -> dict[str, Any] | JSONResponse:
    body = body or RunIn()
    if not body.wait:
        services = request.app.state.services
        await services.definitions.get(def_id)  # 404 sofort, nicht erst im Auftrag
        if body.instance is not None:
            try:
                services.client(body.instance)
            except KeyError:
                raise UnknownInstance(body.instance) from None

        async def work(run_id: str, progress) -> None:
            await services.runner.run(def_id, refresh=body.refresh, instance=body.instance,
                                      run_id=run_id, progress=progress)

        return JSONResponse(services.jobs.start(def_id, work).view(), status_code=202)
    run = await _runner(request).run(
        def_id, refresh=body.refresh, instance=body.instance, save=body.save)
    if body.report:
        run["report"] = report_of(run)
    return run


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
    status: Literal["ok", "partial", "failed"] | None = None,
    kind: Literal["metric", "dashboard", "template"] | None = None,
) -> list[dict[str, Any]]:
    """``definition=<id>`` nur Läufe dieser Definition (auch einer gelöschten nicht mehr:
    deren Läufe liefert ``definition=none``). Weniger Einträge als ``limit`` heißt: Ende."""
    wanted = "" if definition == "none" else definition
    return await _runner(request).runs(
        wanted, limit=limit, before=before, status=status, kind=kind)


@router.delete("/runs")
async def delete_runs(
    request: Request, definition: Annotated[str, Query(min_length=1, max_length=64)],
) -> dict[str, int]:
    """Löscht alle Läufe einer Definition (``none``: die Läufe gelöschter Definitionen).
    Ohne ``definition`` wird nichts gelöscht."""
    wanted = "" if definition == "none" else definition
    return {"deleted": await _runner(request).delete_runs(wanted)}


@router.get("/runs/{run_id}")
async def get_run(run_id: str, request: Request) -> dict[str, Any]:
    """Ein laufender Auftrag antwortet mit ``status: "running"`` und ``progress``, ein
    gescheiterter eine Weile mit ``status: "failed"`` und ``error``."""
    job = request.app.state.services.jobs.get(run_id)
    if job is not None:
        return job.view()
    return await _runner(request).get_run(run_id)


@router.delete("/runs/{run_id}", status_code=204)
async def delete_run(run_id: str, request: Request) -> Response:
    await _runner(request).delete_run(run_id)
    return Response(status_code=204)
