"""App-Fabrik: hängt die API unter ``/api`` ein und liefert die Oberfläche aus."""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path

from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, Response

from . import __version__
from .api import auth, health, instances
from .api import settings as settings_api
from .config import Settings, load_settings
from .deps import require_admin
from .instances import InstanceRegistry
from .security import LoginThrottle, SessionSigner, resolve_session_secret

# Die einzigen API-Pfade, die ohne Admin-Session antworten. Alles andere hängt am
# geschützten Router; tests/test_routes_guard.py prüft das für jede Route.
PUBLIC_API_PATHS: frozenset[str] = frozenset(
    {"/api/health", "/api/auth/login", "/api/auth/status", "/api/auth/logout"}
)

_INDEX = Path(__file__).parent / "static" / "index.html"


def create_app(
    settings: Settings | None = None,
    *,
    clock: Callable[[], float] = time.monotonic,
) -> FastAPI:
    settings = settings or load_settings()
    app = FastAPI(
        title="WLO-Daten-Dashboard",
        version=__version__,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.settings = settings
    app.state.instances = InstanceRegistry(settings.instances)
    app.state.signer = SessionSigner(resolve_session_secret(settings), settings.session_max_age)
    app.state.throttle = LoginThrottle(clock=clock)

    public = APIRouter(prefix="/api")
    public.include_router(health.router)
    public.include_router(auth.router)

    protected = APIRouter(prefix="/api", dependencies=[Depends(require_admin)])
    protected.include_router(instances.router)
    protected.include_router(settings_api.router)

    app.include_router(public)
    app.include_router(protected)

    @app.middleware("http")
    async def security_headers(request: Request, call_next) -> Response:
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        if request.url.path.startswith("/api"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.api_route("/api/{rest:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
                   include_in_schema=False, dependencies=[Depends(require_admin)])
    def api_not_found(rest: str) -> JSONResponse:
        return JSONResponse({"detail": "Nicht gefunden"}, status_code=404)

    # Die Oberfläche ist genau eine Datei aus dem Paket. Es gibt bewusst kein
    # Verzeichnis-Mount: kein Pfad kann auf das Datenverzeichnis zeigen.
    @app.get("/{rest:path}", include_in_schema=False)
    def spa(rest: str) -> FileResponse:
        return FileResponse(_INDEX, media_type="text/html", headers={"Cache-Control": "no-cache"})

    return app
