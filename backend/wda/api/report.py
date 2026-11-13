"""Berichte für die Oberfläche: das Berichtsmodell eines Laufs als JSON, die Vorschau einer
ungespeicherten Definition und die Prüfung einer Definition ohne Speichern.

Wie beim Export entsteht alles nur aus Zählungen, Definitionen und deren Texten.
"""

from __future__ import annotations

import logging
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictStr

from ..report.build import build_report
from ..report.formats import report_data
from ..store.runner import DamagedRun
from ..store.specs import check_spec

router = APIRouter()
log = logging.getLogger(__name__)
Kind = Literal["filter", "metric", "dashboard", "template"]


def report_of(run: dict[str, Any]) -> dict[str, Any]:
    try:
        return report_data(build_report(run), with_html=True)
    except (KeyError, TypeError, ValueError, IndexError, AttributeError, OverflowError):
        log.exception("Bericht des Laufs %s ließ sich nicht bauen", run.get("id"))
        raise DamagedRun(
            "Der Lauf ist beschädigt oder hat ein unerwartetes Format und lässt sich "
            "nicht darstellen. Bitte die Definition neu ausführen.") from None


class PreviewIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Kind
    name: Annotated[StrictStr, Field(max_length=200)] = "Vorschau"
    spec: dict[str, Any]
    instance: Annotated[StrictStr, Field(min_length=1, max_length=64)] | None = None
    refresh: StrictBool = False


class CheckIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Kind
    spec: Any


@router.get("/runs/{run_id}/report")
async def run_report(run_id: str, request: Request) -> dict[str, Any]:
    return report_of(await request.app.state.services.runner.get_run(run_id))


@router.post("/preview")
async def preview(body: PreviewIn, request: Request) -> dict[str, Any]:
    run = await request.app.state.services.runner.preview(
        body.kind, body.name.strip() or "Vorschau", body.spec,
        refresh=body.refresh, instance=body.instance)
    return {"run": run, "report": report_of(run)}


@router.post("/check")
def check(body: CheckIn) -> dict[str, Any]:
    errors = check_spec(body.kind, body.spec)
    return {"ok": not errors, "errors": errors}
