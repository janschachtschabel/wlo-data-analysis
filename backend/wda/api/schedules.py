"""Zeitpläne: Definitionen von selbst laufen lassen.

Ein Zeitplan enthält nur den Bezug auf eine Definition und einen Rhythmus, keine Daten.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr

from ..errors import QueryError, UnknownInstance

router = APIRouter()

At = Annotated[StrictStr, Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")]


class Rhythm(BaseModel):
    model_config = ConfigDict(extra="forbid")
    every: Literal["daily", "weekly", "monthly"]
    at: At
    weekday: Annotated[StrictInt, Field(ge=0, le=6)] | None = None
    day: Annotated[StrictInt, Field(ge=1, le=28)] | None = None
    enabled: StrictBool = True


class ScheduleIn(Rhythm):
    definition_id: Annotated[StrictStr, Field(min_length=1, max_length=64)]
    instance: Annotated[StrictStr, Field(min_length=1, max_length=64)] | None = None


def _store(request: Request):
    return request.app.state.services.schedules


def _now(request: Request):
    return request.app.state.scheduler_clock()


@router.get("/schedules")
async def list_schedules(
    request: Request, definition: Annotated[str | None, Query(max_length=64)] = None,
) -> list[dict[str, Any]]:
    return await _store(request).list(definition)


@router.post("/schedules", status_code=201)
async def create_schedule(body: ScheduleIn, request: Request) -> dict[str, Any]:
    if body.instance is not None:
        try:
            request.app.state.services.client(body.instance)
        except KeyError:
            raise UnknownInstance(body.instance) from None
    return await _store(request).create(
        definition_id=body.definition_id, instance=body.instance, every=body.every,
        at=body.at, weekday=body.weekday, day=body.day, enabled=body.enabled,
        now=_now(request))


@router.put("/schedules/{schedule_id}")
async def update_schedule(schedule_id: str, body: Rhythm, request: Request) -> dict[str, Any]:
    return await _store(request).update(
        schedule_id, every=body.every, at=body.at, weekday=body.weekday, day=body.day,
        enabled=body.enabled, now=_now(request))


@router.delete("/schedules/{schedule_id}", status_code=204)
async def delete_schedule(schedule_id: str, request: Request) -> Response:
    await _store(request).delete(schedule_id)
    return Response(status_code=204)


@router.post("/schedules/{schedule_id}/run")
async def run_schedule(schedule_id: str, request: Request) -> JSONResponse:
    """Führt den Zeitplan jetzt als Auftrag aus; der nächste Termin bleibt, wie er ist."""
    schedule = await _store(request).get(schedule_id)
    scheduler = request.app.state.scheduler

    async def work(run_id: str, progress) -> None:
        run = await scheduler.execute(
            schedule, advance=False, run_id=run_id, progress=progress)
        if run is None:
            raise QueryError("Der Lauf ist gescheitert; der Grund steht am Zeitplan.")

    job = request.app.state.services.jobs.start(schedule["definition_id"], work)
    return JSONResponse(job.view(), status_code=202)
