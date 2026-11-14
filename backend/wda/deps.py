"""Gemeinsame Abhängigkeiten der API-Routen."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException, Request, status
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from .security import COOKIE_NAME


def is_admin(request: Request) -> bool:
    return request.app.state.signer.valid(request.cookies.get(COOKIE_NAME))


MAX_BODY_BYTES = 6 * 1024 * 1024  # der Import erlaubt 5 MB, sonst braucht nichts so viel
# Eine Vorlage für den Assistenten: 8 MB Datei, als Base64 ein Drittel mehr
BODY_LIMITS = {"/api/assistant/document": 12 * 1024 * 1024}


class _TooLarge(Exception):
    pass


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
        length = dict(scope["headers"]).get(b"content-length", b"")
        limit = BODY_LIMITS.get(path, MAX_BODY_BYTES)
        too_large = JSONResponse(
            {"detail": "Die Anfrage ist zu groß.", "kind": "too_large"}, status_code=413)
        if length.isdigit() and int(length) > limit:
            await too_large(scope, receive, send)
            return
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
        # ohne Content-Length (in Stücken gesendet) zählt die Grenze beim Lesen
        seen = 0
        over = started = False

        async def counted() -> Any:
            nonlocal seen, over
            message = await receive()
            if message["type"] == "http.request":
                seen += len(message.get("body", b""))
                if seen > limit:
                    over = True
                    raise _TooLarge
            return message

        async def tracked(message: Any) -> None:
            nonlocal started
            if over and not started:
                return  # die Antwort der Route auf den Abbruch zählt nicht
            started = started or message["type"] == "http.response.start"
            await send(message)

        try:
            await self.app(scope, counted, tracked)
        except _TooLarge:
            pass
        if over and not started:
            await too_large(scope, receive, send)


def require_admin(request: Request) -> None:
    if not is_admin(request):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Nicht angemeldet")
