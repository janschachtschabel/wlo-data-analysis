"""Zeitpläne: Definitionen von selbst laufen lassen.

Ein Zeitplan enthält nur den Bezug auf eine Definition und einen Rhythmus, keine Daten.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, Query, Request, Response
from fastapi.responses import JSONResponse
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    StrictStr,
    model_validator,
)

from ..errors import QueryError, UnknownInstance
from ..store.definitions import Conflict

router = APIRouter()

At = Annotated[StrictStr, Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")]


Mail = Annotated[StrictStr, Field(max_length=200,
                                  pattern=r"^[^@\s,;<>\"']+@[^@\s,;<>\"']+\.[^@\s,;<>\"']+$")]


class Send(BaseModel):
    """Versand nach dem Lauf. Verschickt wird der Bericht, also Auswertungen."""
    model_config = ConfigDict(extra="forbid")
    to: Annotated[list[Mail], Field(max_length=10)] = []
    format: Literal["pdf", "docx", "xlsx", "html"] = "pdf"
    webhook: Annotated[StrictStr, Field(min_length=8, max_length=500)] | None = None
    only_notable: StrictBool = False

    @model_validator(mode="after")
    def _somewhere(self) -> Send:
        if not self.to and not self.webhook:
            raise ValueError("Der Versand braucht mindestens eine E-Mail-Adresse oder "
                             "einen Webhook.")
        if self.webhook is not None:
            parts = urlsplit(self.webhook)
            if (parts.scheme not in ("http", "https") or not parts.hostname
                    or parts.username or parts.password):
                raise ValueError("Der Webhook ist eine Adresse mit http:// oder https://, "
                                 "ohne Zugangsdaten in der Adresse.")
        return self


class Rhythm(BaseModel):
    model_config = ConfigDict(extra="forbid")
    every: Literal["daily", "weekly", "monthly"]
    at: At
    weekday: Annotated[StrictInt, Field(ge=0, le=6)] | None = None
    day: Annotated[StrictInt, Field(ge=1, le=28)] | None = None
    enabled: StrictBool = True
    send: Send | None = None


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
        now=_now(request), send=body.send.model_dump() if body.send else None)


@router.put("/schedules/{schedule_id}")
async def update_schedule(schedule_id: str, body: Rhythm, request: Request) -> dict[str, Any]:
    return await _store(request).update(
        schedule_id, every=body.every, at=body.at, weekday=body.weekday, day=body.day,
        enabled=body.enabled, now=_now(request),
        send=body.send.model_dump() if body.send else None)


@router.delete("/schedules/{schedule_id}", status_code=204)
async def delete_schedule(schedule_id: str, request: Request) -> Response:
    await _store(request).delete(schedule_id)
    return Response(status_code=204)


@router.post("/schedules/{schedule_id}/run")
async def run_schedule(schedule_id: str, request: Request) -> JSONResponse:
    """Führt den Zeitplan jetzt als Auftrag aus; der nächste Termin bleibt, wie er ist."""
    schedule = await _store(request).get(schedule_id)
    scheduler = request.app.state.scheduler
    if scheduler.running(schedule_id):
        raise Conflict("Dieser Zeitplan läuft gerade; bitte das Ende abwarten.")

    async def work(run_id: str, progress) -> None:
        run = await scheduler.execute(
            schedule, advance=False, run_id=run_id, progress=progress)
        if not run:
            raise QueryError("Der Lauf ist gescheitert; der Grund steht am Zeitplan.")

    job = request.app.state.services.jobs.start(schedule["definition_id"], work)
    return JSONResponse(job.view(), status_code=202)
