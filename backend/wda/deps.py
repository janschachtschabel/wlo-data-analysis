"""Gemeinsame Abhängigkeiten der API-Routen."""

from __future__ import annotations

from fastapi import HTTPException, Request, status
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from .security import COOKIE_NAME


def is_admin(request: Request) -> bool:
    return request.app.state.signer.valid(request.cookies.get(COOKIE_NAME))


class AdminGate:
    """Sperrt alles unter ``/api`` außer den öffentlichen Pfaden, bevor eine Route gewählt
    wird. Eine Route kann den Schutz damit nicht vergessen; ``require_admin`` an den Routen
    bleibt als zweite Sicherung. Andere Verbindungsarten (WebSocket) gibt es nicht."""

    def __init__(self, app: ASGIApp, public_paths: frozenset[str]) -> None:
        self.app = app
        self.public_paths = public_paths

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "lifespan":
            await self.app(scope, receive, send)
            return
        if scope["type"] != "http":
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1008})
            return
        path = scope["path"]
        if (path == "/api" or path.startswith("/api/")) and path not in self.public_paths:
            request = Request(scope)
            if not is_admin(request):
                response = JSONResponse({"detail": "Nicht angemeldet", "kind": "unauthenticated"},
                                        status_code=401,
                                        headers={"Cache-Control": "no-store"})
                await response(scope, receive, send)
                return
        elif not (path == "/api" or path.startswith("/api/")):
            # Außerhalb von /api gibt es nur die Oberfläche: lesen ja, sonst nichts.
            if scope["method"] not in ("GET", "HEAD"):
                response = JSONResponse(
                    {"detail": "Nicht erlaubt", "kind": "method_not_allowed"}, status_code=405)
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)


def require_admin(request: Request) -> None:
    if not is_admin(request):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Nicht angemeldet")
