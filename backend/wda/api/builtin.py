"""Mitgelieferte Definitionen (z. B. die OER-Statistik) anzeigen und anlegen."""

from __future__ import annotations

import json
from functools import cache
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request

from ..store.specs import InvalidDefinition, dump_spec, parse_spec

router = APIRouter()
_DIR = Path(__file__).resolve().parent.parent / "builtin"


@cache
def bundles() -> dict[str, dict[str, Any]]:
    found = {}
    for path in sorted(_DIR.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        found[data["builtin"]["id"]] = data
    return found


@cache
def _shipped(bundle_id: str, def_id: str) -> tuple[str, str, str] | None:
    """Name, Beschreibung und Spezifikation in der Form, in der sie gespeichert würden."""
    for entry in bundles()[bundle_id]["definitions"]:
        if entry["id"] == def_id:
            try:
                spec = dump_spec(parse_spec(entry["kind"], entry["spec"]))
            except InvalidDefinition:
                return None
            return entry["name"], entry.get("description", ""), spec
    return None


@router.get("/builtin")
async def list_builtin(request: Request) -> list[dict[str, Any]]:
    """``changed``: Die installierte Fassung weicht von der mitgelieferten ab (eigene
    Änderung oder neuere Vorlage); ``install?mode=replace`` setzt sie zurück."""
    store = request.app.state.services.definitions
    existing = {d["id"] for d in await store.list()}
    out = []
    for bundle_id, data in bundles().items():
        items = []
        for d in data["definitions"]:
            changed = False
            if d["id"] in existing:
                have = await store.get(d["id"])
                mine = (have.name, have.description,
                        json.dumps(have.spec, ensure_ascii=False, sort_keys=True))
                changed = _shipped(bundle_id, d["id"]) != mine
            items.append({"id": d["id"], "kind": d["kind"], "name": d["name"],
                          "installed": d["id"] in existing, "changed": changed})
        out.append({**data["builtin"], "definitions": items})
    return out


@router.post("/builtin/{bundle_id}/install")
async def install_builtin(
    bundle_id: str, request: Request,
    mode: Annotated[Literal["skip", "replace"], Query()] = "skip",
) -> dict[str, Any]:
    """Legt die Definitionen an. Was es schon gibt, bleibt unverändert (auch eigene
    Änderungen daran); ``mode=replace`` setzt es auf die mitgelieferte Fassung zurück."""
    data = bundles().get(bundle_id)
    if data is None:
        raise HTTPException(404, "Diese mitgelieferte Definition gibt es nicht.")
    return await request.app.state.services.transfer.import_(data, mode)
