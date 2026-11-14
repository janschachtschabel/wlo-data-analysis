"""Der lokale Abzug einer Instanz: Stand, Start, Stopp, Löschen. Keine dieser Routen gibt
etwas aus dem Abzug heraus; ausgewertet wird er nur über Abfragen (``snapshot``)."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any, Literal

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from ..errors import UnknownInstance, error_body
from ..snapshot.store import Snapshot
from ..snapshot.sync import status, sync

log = logging.getLogger(__name__)
router = APIRouter(prefix="/snapshot")


class StartIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["full", "update"] = "full"


def _snapshot(request: Request, instance: str) -> Snapshot:
    try:
        return request.app.state.services.snapshot(instance)
    except KeyError:
        raise UnknownInstance(instance) from None


def _running(request: Request, instance: str) -> bool:
    task = request.app.state.services.syncs.get(instance)
    return task is not None and not task.done()


@router.get("/{instance}")
async def read(instance: str, request: Request) -> dict[str, Any]:
    snap = _snapshot(request, instance)
    return await asyncio.to_thread(status, snap, _running(request, instance))


@router.post("/{instance}/start", status_code=202)
async def start(instance: str, body: StartIn, request: Request) -> Any:
    services = request.app.state.services
    snap = _snapshot(request, instance)
    if _running(request, instance):
        return JSONResponse(error_body("conflict", "Der Abzug läuft schon."), status_code=409)
    if body.mode == "update" and "finished_at" not in snap.state():
        return JSONResponse(error_body(
            "conflict", "Nachziehen geht erst nach einem vollständigen Abzug."),
            status_code=409)
    client, secret = services.client(instance), services.snapshot_secret

    async def go() -> None:
        # Fehler stehen im Stand des Abzugs; die Aufgabe selbst endet still
        with contextlib.suppress(Exception):
            await sync(client, snap, secret, body.mode,
                       page=request.app.state.settings.snapshot_page)

    snap.set_state(status="running", mode=body.mode, error=None)
    services.syncs[instance] = asyncio.create_task(go())
    return await asyncio.to_thread(status, snap, True)


@router.post("/{instance}/stop")
async def stop(instance: str, request: Request) -> dict[str, Any]:
    snap = _snapshot(request, instance)
    task = request.app.state.services.syncs.get(instance)
    if task is not None and not task.done():
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    return await asyncio.to_thread(status, snap, False)


@router.delete("/{instance}")
async def clear(instance: str, request: Request) -> Any:
    snap = _snapshot(request, instance)
    if _running(request, instance):
        return JSONResponse(error_body("conflict", "Erst den laufenden Abzug anhalten."),
                            status_code=409)
    await asyncio.to_thread(snap.clear)
    return await asyncio.to_thread(status, snap, False)
