"""Mitgelieferte Definitionen (z. B. die OER-Statistik) anzeigen und anlegen."""

from __future__ import annotations

import json
from functools import cache
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request

router = APIRouter()
_DIR = Path(__file__).resolve().parent.parent / "builtin"


@cache
def bundles() -> dict[str, dict[str, Any]]:
    found = {}
    for path in sorted(_DIR.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        found[data["builtin"]["id"]] = data
    return found


@router.get("/builtin")
async def list_builtin(request: Request) -> list[dict[str, Any]]:
    existing = {d["id"] for d in await request.app.state.services.definitions.list()}
    return [{**data["builtin"],
             "definitions": [{"id": d["id"], "kind": d["kind"], "name": d["name"],
                              "installed": d["id"] in existing} for d in data["definitions"]]}
            for data in bundles().values()]


@router.post("/builtin/{bundle_id}/install")
async def install_builtin(bundle_id: str, request: Request) -> dict[str, Any]:
    """Legt die Definitionen an. Was es schon gibt, bleibt unverändert (auch eigene
    Änderungen daran); eine frische Kopie entsteht über Duplizieren oder Import."""
    data = bundles().get(bundle_id)
    if data is None:
        raise HTTPException(404, "Diese mitgelieferte Definition gibt es nicht.")
    return await request.app.state.services.transfer.import_(data, "skip")
