"""Gemeinsame Abhängigkeiten der API-Routen."""

from __future__ import annotations

from fastapi import HTTPException, Request, status

from .security import COOKIE_NAME


def is_admin(request: Request) -> bool:
    return request.app.state.signer.valid(request.cookies.get(COOKIE_NAME))


def require_admin(request: Request) -> None:
    if not is_admin(request):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Nicht angemeldet")
