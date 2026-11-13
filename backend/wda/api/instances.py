from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

router = APIRouter(prefix="/instances")


@router.get("")
def list_instances(request: Request) -> list[dict[str, Any]]:
    return [inst.public() for inst in request.app.state.instances.list()]
