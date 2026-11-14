"""App-Fabrik: hängt die API unter ``/api`` ein und liefert die Oberfläche aus."""

from __future__ import annotations

import asyncio
import contextlib
import mimetypes
import re
import time
from collections.abc import Callable
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

import httpx
from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, Response

from . import __version__
from .api import (
    assistant,
    auth,
    builtin,
    definitions,
    edusharing,
    errors,
    export,
    health,
    history,
    instances,
    query,
    report,
    runs,
    schedules,
)
from .api import settings as settings_api
from .config import Settings, load_settings
from .deps import AdminGate, require_admin
from .instances import InstanceRegistry
from .jobs import TooManyJobs
from .scheduler import Scheduler, utc_now
from .security import LoginThrottle, SessionSigner, session_signing_key
from .services import Services

# Die einzigen API-Pfade, die ohne Admin-Session antworten. Alles andere hängt am
# geschützten Router; tests/test_routes_guard.py prüft das für jede Route.
PUBLIC_API_PATHS: frozenset[str] = frozenset(
    {"/api/health", "/api/auth/login", "/api/auth/status", "/api/auth/logout"}
)

_STATIC = Path(__file__).parent / "static"
# Solange kein Bau der Oberfläche im Paket liegt, erscheint diese eine Seite.
_INDEX = Path(__file__).parent / "placeholder.html"
_HASHED = re.compile(r"[-.][A-Za-z0-9_-]{8,}\.(?:js|css|woff2?|svg|png|jpg|webp|ico)$")
# Die Oberfläche lädt nur vom eigenen Ursprung. Angular setzt Komponenten-Stile als
# <style>-Elemente, deshalb 'unsafe-inline' nur für Stile.
_SPA_CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: blob:; font-src 'self'; connect-src 'self'; "
            "object-src 'none'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'")


def create_app(
    settings: Settings | None = None,
    *,
    clock: Callable[[], float] = time.monotonic,
    transport: httpx.AsyncBaseTransport | None = None,
    llm_transport: httpx.AsyncBaseTransport | None = None,
    static_dir: Path | None = None,
    now: Callable[[], datetime] = utc_now,
) -> FastAPI:
    settings = settings or load_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Die Ablage beim Start öffnen: Eine unbrauchbare oder neuere Datenbank bricht den
        # Start mit Meldung ab, statt später jede Anfrage scheitern zu lassen.
        _ = app.state.services.db
        planner = (asyncio.create_task(app.state.scheduler.run_forever())
                   if settings.scheduler else None)
        yield
        if planner is not None:
            planner.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await planner
        await app.state.services.aclose()

    app = FastAPI(
        lifespan=lifespan,
        title="WLO-Daten-Dashboard",
        version=__version__,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.settings = settings
    app.state.instances = InstanceRegistry(settings.instances)
    app.state.signer = SessionSigner(session_signing_key(settings), settings.session_max_age)
    app.state.throttle = LoginThrottle(clock=clock)
    app.state.services = Services(settings, app.state.instances, transport=transport,
                                  llm_transport=llm_transport)

    app.state.scheduler_clock = now
    app.state.scheduler = Scheduler(
        lambda: app.state.services.schedules, lambda: app.state.services.runner, clock=now)

    @app.exception_handler(TooManyJobs)
    async def too_many_jobs(request: Request, exc: TooManyJobs) -> JSONResponse:
        return JSONResponse({"detail": str(exc), "kind": "too_many_jobs"}, status_code=429)

    public = APIRouter(prefix="/api")
    public.include_router(health.router)
    public.include_router(auth.router)

    protected = APIRouter(prefix="/api", dependencies=[Depends(require_admin)])
    protected.include_router(instances.router)
    protected.include_router(settings_api.router)
    protected.include_router(edusharing.router)
    edusharing.register_error_handlers(app)
    protected.include_router(query.router)
    protected.include_router(definitions.router)
    protected.include_router(runs.router)
    protected.include_router(history.router)
    protected.include_router(schedules.router)
    protected.include_router(export.router)
    protected.include_router(report.router)
    protected.include_router(builtin.router)
    protected.include_router(assistant.router)
    query.register_error_handlers(app)
    errors.register_error_handlers(app)

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

    app.add_middleware(AdminGate, public_paths=PUBLIC_API_PATHS)

    methods = ["GET", "POST", "PUT", "PATCH", "DELETE"]

    @app.api_route("/api", methods=methods, dependencies=[Depends(require_admin)])
    @app.api_route("/api/{rest:path}", methods=methods, dependencies=[Depends(require_admin)])
    def api_not_found(rest: str = "") -> JSONResponse:
        return JSONResponse({"detail": "Nicht gefunden", "kind": "not_found"}, status_code=404)

    # Die Oberfläche: genau die Dateien, die beim Start im Paket-Ordner ``static`` liegen.
    # Es gibt bewusst kein Verzeichnis-Mount; ein Pfad wird nur als Schlüssel dieser Liste
    # benutzt und kann deshalb nie auf das Datenverzeichnis zeigen.
    root = (static_dir or _STATIC).resolve()
    files = {
        path.relative_to(root).as_posix(): path
        for path in sorted(root.rglob("*"))
        if path.is_file() and not path.is_symlink()
        and not any(part.startswith(".") for part in path.relative_to(root).parts)
    } if root.is_dir() else {}
    index = files.pop("index.html", _INDEX)

    @app.get("/{rest:path}", include_in_schema=False)
    def spa(rest: str) -> Response:
        found = files.get(rest)
        if found is not None:
            hashed = _HASHED.search(found.name) is not None
            media_type = mimetypes.guess_type(found.name)[0] or "application/octet-stream"
            return FileResponse(found, media_type=media_type, headers={
                "Cache-Control": "public, max-age=31536000, immutable" if hashed
                else "no-cache"})
        if "." in rest.rsplit("/", 1)[-1] and rest != "index.html":
            # eine fehlende Datei ist kein HTML (sonst läse der Browser es als Skript)
            return JSONResponse({"detail": "Nicht gefunden", "kind": "not_found"},
                                status_code=404)
        return FileResponse(index, media_type="text/html", headers={
            "Cache-Control": "no-cache", "Content-Security-Policy": _SPA_CSP})

    return app
