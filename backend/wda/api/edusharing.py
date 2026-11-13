"""Routen je Instanz: Erreichbarkeit, Felder, Vokabular und Aggregat-Abfragen.

Keine dieser Routen kann Datensätze liefern; der Client fordert nur Zählungen an.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictStr

from ..edusharing.client import MAX_FACET_LIMIT, EduSharingClient
from ..edusharing.errors import EduSharingError
from ..edusharing.vocab import UnresolvedValue, VocabStore, _norm
from ..filters import FilterIn, Name, resolve_filters

router = APIRouter(prefix="/instances/{instance_id}")

# Fehler, die an der Abfrage liegen, sind 422; alles andere liegt am Repositorium.
_STATUS = {"timeout": 504, "invalid_query": 422, "unknown_property": 422,
           "unknown_facet": 422, "single_value": 422}


def status_for(exc: EduSharingError) -> int:
    if exc.kind == "rejected" and exc.status == 400:
        return 422  # Grund vom Repositorium verborgen, aber es lehnt die Abfrage ab
    return _STATUS.get(exc.kind, 502)


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(EduSharingError)
    async def upstream_error(request: Request, exc: EduSharingError) -> JSONResponse:
        return JSONResponse(
            {"detail": exc.message, "kind": exc.kind, "upstream_status": exc.status},
            status_code=status_for(exc))

    @app.exception_handler(UnresolvedValue)
    async def unresolved(request: Request, exc: UnresolvedValue) -> JSONResponse:
        return JSONResponse(
            {"detail": str(exc), "kind": "unresolved_value", "property": exc.property,
             "text": exc.text, "ambiguous": exc.ambiguous, "suggestions": exc.suggestions},
            status_code=422)


def _services(request: Request, instance_id: str) -> tuple[EduSharingClient, VocabStore]:
    services = request.app.state.services
    try:
        return services.client(instance_id), services.vocab(instance_id)
    except KeyError:
        raise HTTPException(404, "Instanz nicht gefunden") from None


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
    criteria, resolved_filters = await resolve_filters(vocab, body.filters)

    notes = [
        f"'{c.property}' trifft als Filter per Wortanfang: '{c.values[0]}' zählt auch alle "
        "Schlüssel mit, die so beginnen. Exakte Zahlen je Schlüssel stehen in der Facette."
        for c in criteria if c.property in client.instance.prefix_fields]
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
        "notes": notes,
        "fetched_at": result.fetched_at,
        "cached": result.cached,
    }
