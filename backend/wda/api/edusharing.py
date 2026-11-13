"""Routen je Instanz: Erreichbarkeit, Felder, Vokabular und Aggregat-Abfragen.

Keine dieser Routen kann Datensätze liefern; der Client fordert nur Zählungen an.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictStr

from ..edusharing.client import (
    MAX_FACET_LIMIT,
    MAX_VALUE_LENGTH,
    Criterion,
    EduSharingClient,
)
from ..edusharing.errors import EduSharingError
from ..edusharing.vocab import UnresolvedValue, VocabStore, _norm

router = APIRouter(prefix="/instances/{instance_id}")

_STATUS = {"timeout": 504, "invalid_query": 422}


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(EduSharingError)
    async def upstream_error(request: Request, exc: EduSharingError) -> JSONResponse:
        return JSONResponse(
            {"detail": exc.message, "kind": exc.kind, "upstream_status": exc.status},
            status_code=_STATUS.get(exc.kind, 502))

    @app.exception_handler(UnresolvedValue)
    async def unresolved(request: Request, exc: UnresolvedValue) -> JSONResponse:
        return JSONResponse(
            {"detail": str(exc), "property": exc.property, "text": exc.text,
             "ambiguous": exc.ambiguous, "suggestions": exc.suggestions},
            status_code=422)


def _services(request: Request, instance_id: str) -> tuple[EduSharingClient, VocabStore]:
    services = request.app.state.services
    try:
        return services.client(instance_id), services.vocab(instance_id)
    except KeyError:
        raise HTTPException(404, "Instanz nicht gefunden") from None


Name = Annotated[StrictStr, Field(min_length=1, max_length=200, pattern=r"^\S+$")]
Value = Annotated[StrictStr, Field(max_length=MAX_VALUE_LENGTH)]


class LiteralValue(BaseModel):
    """Wert, der unverändert als Filter gilt, ohne Abgleich mit dem Vokabular."""

    model_config = ConfigDict(extra="forbid")
    value: Value
    literal: Literal[True]


class FilterIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    property: Name
    values: list[Value | LiteralValue] = Field(min_length=1, max_length=2000)


class AggregateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    filters: list[FilterIn] = Field(default_factory=list, max_length=20)
    facets: list[Name] = Field(default_factory=list, max_length=12)
    facet_limit: int = Field(default=2000, ge=1, le=MAX_FACET_LIMIT)
    content_type: StrictStr = "FILES"
    refresh: bool = False


@router.get("/about")
async def about(instance_id: str, request: Request) -> dict[str, Any]:
    client, _ = _services(request, instance_id)
    try:
        version = await client.about()
        total = (await client.aggregate([])).total
    except EduSharingError as exc:
        return {"instance": instance_id, "reachable": False,
                "error": {"detail": exc.message, "kind": exc.kind,
                          "upstream_status": exc.status}}
    return {"instance": instance_id, "reachable": True, "version": version, "total": total}


@router.get("/fields")
async def fields(instance_id: str, request: Request) -> list[dict[str, Any]]:
    _, vocab = _services(request, instance_id)
    return [
        {"property": f.property, "caption": f.caption, "type": f.type,
         "value_count": len(f.values)}
        for f in (await vocab.fields()).values()
    ]


@router.get("/vocab")
async def vocabulary(
    instance_id: str,
    request: Request,
    property: Annotated[str, Query(min_length=1, max_length=200)],
    q: Annotated[str, Query(max_length=200)] = "",
    limit: Annotated[int, Query(ge=1, le=5000)] = 50,
) -> dict[str, Any]:
    _, vocab = _services(request, instance_id)
    field = await vocab.field(property)
    if field is None:
        raise HTTPException(404, "Feld nicht gefunden")
    values = list(field.values)
    needle = _norm(q)
    if needle:
        values = [v for v in values if needle in _norm(v.caption) or needle in v.id.casefold()]
        values.sort(key=lambda v: (not _norm(v.caption).startswith(needle), len(v.caption)))
    return {
        "property": field.property,
        "caption": field.caption,
        "total": len(values),
        "values": [{"id": v.id, "caption": v.caption, "parent": v.parent}
                   for v in values[:limit]],
    }


@router.post("/aggregate")
async def aggregate(instance_id: str, body: AggregateIn, request: Request) -> dict[str, Any]:
    client, vocab = _services(request, instance_id)
    seen: set[str] = set()
    criteria: list[Criterion] = []
    resolved_filters: list[dict[str, Any]] = []
    for flt in body.filters:
        if flt.property in seen:
            raise EduSharingError(
                "invalid_query",
                f"Das Feld '{flt.property}' kommt in mehreren Filtern vor. "
                "Mehrere Werte gehören in einen Filter.")
        seen.add(flt.property)
        resolved: dict[str, dict[str, str]] = {}
        for item in flt.values:
            if isinstance(item, LiteralValue):
                res = await vocab.resolve(flt.property, item.value, literal=True)
            else:
                res = await vocab.resolve(flt.property, item)
            resolved.setdefault(res.value, {"value": res.value, "label": res.label,
                                            "how": res.how})
        criteria.append(Criterion(flt.property, tuple(resolved)))
        resolved_filters.append({"property": flt.property, "values": list(resolved.values())})

    facet_props = list(dict.fromkeys(body.facets))
    for prop in facet_props:
        await vocab.check_facet(prop)
    result = await client.aggregate(
        criteria, facet_props, facet_limit=body.facet_limit,
        content_type=body.content_type, refresh=body.refresh)

    facets_out = []
    for prop in facet_props:
        facet = result.facets[prop]
        field = await vocab.field(prop)
        facets_out.append({
            "property": prop,
            "caption": field.caption if field else prop,
            "other_count": facet.other_count,
            "truncated": facet.truncated,
            "values": [{"value": v.value, "label": await vocab.label(prop, v.value),
                        "count": v.count} for v in facet.values],
        })
    return {
        "instance": instance_id,
        "total": result.total,
        "facets": facets_out,
        "filters": resolved_filters,
        "fetched_at": result.fetched_at,
        "cached": result.cached,
    }
