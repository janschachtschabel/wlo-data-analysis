"""Berichte für die Oberfläche: das Berichtsmodell eines Laufs als JSON, die Vorschau einer
ungespeicherten Definition und die Prüfung einer Definition ohne Speichern.

Wie beim Export entsteht alles nur aus Zählungen, Definitionen und deren Texten.
"""

from __future__ import annotations

import logging
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictStr

from ..errors import QueryError, error_body
from ..report.build import build_report
from ..report.compare import apply_previous
from ..report.formats import report_data
from ..report.model import Report
from ..store.runner import DamagedRun
from ..store.specs import check_spec, spec_hints

router = APIRouter()
log = logging.getLogger(__name__)
Kind = Literal["filter", "metric", "dashboard", "template"]


Compare = Annotated[str | None, Query(min_length=1, max_length=64)]


async def built_report(request: Request, run: dict[str, Any], compare: str | None) -> Report:
    """Der Bericht eines gespeicherten Laufs, auf Wunsch mit den Werten eines früheren."""
    report = build_report(run)
    if compare is not None:
        current, head, points = await request.app.state.services.history.compare(
            run["id"], compare)
        apply_previous(report, head, points, current)
    if (run.get("id") and any(r.get("result") is not None for r in run.get("results", []))
            and not await request.app.state.services.history.has_points(run["id"])):
        report.notes.append({
            "code": "no_points",
            "text": "Dieser Lauf hat keine Zahlen für die Historie (zu groß oder nicht "
                    "lesbar); er fehlt im Verlauf und lässt sich nicht als Vergleich nutzen."})
    return report


def report_of(run: dict[str, Any], report: Report | None = None) -> dict[str, Any]:
    try:
        return report_data(report or build_report(run), with_html=True)
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
async def run_report(run_id: str, request: Request, compare: Compare = None) -> Any:
    """``compare``: ``previous``, ein Datum (JJJJ-MM-TT) oder die ID eines Laufs; dann trägt
    jede Zeile die Werte dieses früheren Laufs (``previous``)."""
    run = await request.app.state.services.runner.get_run(run_id)
    try:
        report = await built_report(request, run, compare)
    except QueryError as exc:
        return JSONResponse(error_body("invalid_request", str(exc)), status_code=422)
    except (KeyError, TypeError, ValueError, IndexError, AttributeError, OverflowError):
        log.exception("Bericht des Laufs %s ließ sich nicht bauen", run_id)
        raise DamagedRun(
            "Der Lauf ist beschädigt oder hat ein unerwartetes Format und lässt sich "
            "nicht darstellen. Bitte die Definition neu ausführen.") from None
    return report_of(run, report)


@router.post("/preview")
async def preview(body: PreviewIn, request: Request) -> dict[str, Any]:
    run = await request.app.state.services.runner.preview(
        body.kind, body.name.strip() or "Vorschau", body.spec,
        refresh=body.refresh, instance=body.instance)
    return {"run": run, "report": report_of(run)}


@router.post("/check")
def check(body: CheckIn) -> dict[str, Any]:
    errors = check_spec(body.kind, body.spec)
    return {"ok": not errors, "errors": errors,
            "hints": [] if errors else spec_hints(body.kind, body.spec)}
