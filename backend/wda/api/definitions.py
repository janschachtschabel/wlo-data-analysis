"""Verwaltung: Gruppen, Definitionen (Filter, Kennzahlen, Dashboards, Templates), Import
und Export. Antworten enthalten nur Definitionen, nie Daten aus dem Repositorium."""

from __future__ import annotations

import json
from typing import Annotated, Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr

from ..store.definitions import UNSET
from ..store.specs import InvalidDefinition

router = APIRouter()
MAX_IMPORT_BYTES = 5 * 1024 * 1024
Text = Annotated[StrictStr, Field(max_length=4000)]
Ident = Annotated[StrictStr, Field(max_length=64)]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GroupIn(_Model):
    name: Text


class GroupUpdate(_Model):
    name: Text | None = None
    position: StrictInt | None = Field(default=None, ge=0)


class DefinitionIn(_Model):
    kind: Ident
    name: Text
    description: Text = ""
    group_id: Ident | None = None
    spec: dict[str, Any]


class DefinitionUpdate(_Model):
    version: StrictInt
    name: Text | None = None
    description: Text | None = None
    group_id: Ident | None = None  # ausdrücklich null hängt die Definition aus der Gruppe aus
    spec: dict[str, Any] | None = None


class ExportIn(_Model):
    ids: list[Ident] | None = Field(default=None, max_length=2000)


def _store(request: Request):
    return request.app.state.services.definitions


# ---- Gruppen -------------------------------------------------------------------------

@router.get("/groups")
async def list_groups(request: Request) -> list[dict[str, Any]]:
    return await _store(request).groups()


@router.post("/groups", status_code=201)
async def create_group(body: GroupIn, request: Request) -> dict[str, Any]:
    return await _store(request).create_group(body.name)


@router.put("/groups/{group_id}")
async def update_group(group_id: str, body: GroupUpdate, request: Request) -> dict[str, Any]:
    changes = {k: getattr(body, k) for k in body.model_fields_set if getattr(body, k) is not None}
    return await _store(request).update_group(group_id, **changes)


@router.delete("/groups/{group_id}", status_code=204)
async def delete_group(group_id: str, request: Request) -> Response:
    await _store(request).delete_group(group_id)
    return Response(status_code=204)


# ---- Import und Export (vor den Routen mit {def_id}) -----------------------------------

@router.post("/definitions/export")
async def export_definitions(body: ExportIn, request: Request) -> dict[str, Any]:
    return await request.app.state.services.transfer.export(body.ids)


@router.post("/definitions/import")
async def import_definitions(
    request: Request, mode: Literal["skip", "replace", "copy"] = "skip",
) -> dict[str, Any]:
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > MAX_IMPORT_BYTES:
        raise HTTPException(413, "Die Datei ist zu groß (höchstens 5 MB).")
    chunks, size = [], 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > MAX_IMPORT_BYTES:
            raise HTTPException(413, "Die Datei ist zu groß (höchstens 5 MB).")
        chunks.append(chunk)
    try:
        data = json.loads(b"".join(chunks))
    except (ValueError, RecursionError):
        raise InvalidDefinition("Die Datei ist kein gültiges JSON.") from None
    return await request.app.state.services.transfer.import_(data, mode)


# ---- Definitionen --------------------------------------------------------------------

@router.get("/definitions")
async def list_definitions(
    request: Request,
    kind: Annotated[str | None, Query(max_length=40)] = None,
    group: Annotated[str | None, Query(max_length=64)] = None,
    q: Annotated[str | None, Query(max_length=200)] = None,
) -> list[dict[str, Any]]:
    """``group=`` (leer) liefert die Definitionen ohne Gruppe."""
    return await _store(request).list(kind=kind, group_id=group, q=q)


@router.post("/definitions", status_code=201)
async def create_definition(body: DefinitionIn, request: Request) -> dict[str, Any]:
    made = await _store(request).create(
        body.kind, body.name, body.spec, description=body.description, group_id=body.group_id)
    return made.public()


@router.get("/definitions/{def_id}")
async def get_definition(def_id: str, request: Request) -> dict[str, Any]:
    store = _store(request)
    found = await store.get(def_id)
    return {**found.public(), "used_by": await store.used_by(def_id)}


@router.put("/definitions/{def_id}")
async def update_definition(def_id: str, body: DefinitionUpdate, request: Request
                            ) -> dict[str, Any]:
    changes: dict[str, Any] = {}
    for key in ("name", "description", "spec"):
        value = getattr(body, key)
        changes[key] = UNSET if value is None else value
    changes["group_id"] = body.group_id if "group_id" in body.model_fields_set else UNSET
    return (await _store(request).update(def_id, body.version, **changes)).public()


@router.delete("/definitions/{def_id}", status_code=204)
async def delete_definition(def_id: str, request: Request) -> Response:
    await _store(request).delete(def_id)
    return Response(status_code=204)


@router.post("/definitions/{def_id}/duplicate", status_code=201)
async def duplicate_definition(def_id: str, request: Request) -> dict[str, Any]:
    return (await _store(request).duplicate(def_id)).public()
