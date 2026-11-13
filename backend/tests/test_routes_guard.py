import json

from wda.deps import require_admin
from wda.main import PUBLIC_API_PATHS

from .conftest import ADMIN_PASSWORD, INSTANCE_PASSWORD, LLM_KEY


def test_public_paths_are_exactly_these():
    assert PUBLIC_API_PATHS == frozenset({
        "/api/health", "/api/auth/login", "/api/auth/status", "/api/auth/logout"})


def all_routes(routes):
    """Alle Routen der App mit vollem Pfad, auch die aus eingehängten Routern. Je nach
    FastAPI-Version stehen diese direkt in ``app.routes`` oder in einem Router-Objekt."""
    for route in routes:
        if hasattr(route, "effective_route_contexts"):
            for ctx in route.effective_route_contexts():
                yield ctx.path, set(ctx.methods), ctx.dependant, "APIRoute"
        else:
            yield (getattr(route, "path", None), set(getattr(route, "methods", None) or ()),
                   getattr(route, "dependant", None), type(route).__name__)


# Jede neue Route muss hier eingetragen werden. So fällt auf, wenn die Aufzählung der Routen
# (und damit die Prüfung unten) einmal nichts mehr findet.
PROTECTED = {
    "/api", "/api/{rest:path}", "/api/instances", "/api/settings", "/api/query",
    "/api/instances/{instance_id}/about", "/api/instances/{instance_id}/fields",
    "/api/instances/{instance_id}/vocab", "/api/instances/{instance_id}/aggregate",
    "/api/groups", "/api/groups/{group_id}", "/api/definitions", "/api/definitions/export",
    "/api/definitions/import", "/api/definitions/{def_id}",
    "/api/definitions/{def_id}/duplicate", "/api/definitions/{def_id}/run",
    "/api/definitions/{def_id}/runs", "/api/runs", "/api/runs/{run_id}",
    "/api/runs/{run_id}/export", "/api/builtin", "/api/builtin/{bundle_id}/install",
}
OUTSIDE_API = {"/{rest:path}"}  # nur die Oberfläche


def is_api(path):
    return path == "/api" or path.startswith("/api/")


def test_route_list_is_complete(app):
    found = {path for path, *_ in all_routes(app.routes)}
    assert found == PROTECTED | PUBLIC_API_PATHS | OUTSIDE_API


def test_every_other_api_route_requires_admin(app, client):
    checked = set()
    for path, methods, _, _ in all_routes(app.routes):
        if not is_api(path) or path in PUBLIC_API_PATHS:
            continue
        for method in methods - {"HEAD", "OPTIONS"}:
            r = client.request(method, path)
            assert r.status_code == 401, f"{method} {path} ist ohne Login erreichbar"
            checked.add(path)
    assert checked == PROTECTED


def test_guard_is_structural(app):
    """Jede nicht-öffentliche API-Route hängt an require_admin, und es gibt nur gewöhnliche
    Routen (keine Mounts, keine WebSockets)."""
    for path, _, dependant, kind in all_routes(app.routes):
        assert kind == "APIRoute", f"{path}: unerwarteter Routentyp {kind}"
        if not is_api(path) or path in PUBLIC_API_PATHS:
            continue
        calls = {dep.call for dep in dependant.dependencies}
        assert require_admin in calls, f"{path} hängt nicht an require_admin"


def test_gate_protects_routes_that_forgot_the_dependency(settings):
    """Die Sperre sitzt vor der Routenwahl: Auch eine Route ohne require_admin, ein Mount
    oder ein Pfad außerhalb von /api gibt ohne Login nichts her."""
    from fastapi import APIRouter
    from fastapi.testclient import TestClient
    from starlette.responses import PlainTextResponse
    from starlette.routing import Mount, Route, WebSocketRoute

    from wda.main import create_app

    app = create_app(settings)
    leak = APIRouter(prefix="/api/leak")

    @leak.get("/x")
    def x():
        return {"geheim": 1}

    async def plain(request):
        return PlainTextResponse("geheim")

    async def ws(websocket):
        await websocket.accept()
        await websocket.send_text("geheim")
        await websocket.close()

    app.include_router(leak)
    app.router.routes.insert(0, Route("/api/plain", plain, methods=["GET", "POST"]))
    app.router.routes.insert(0, Mount("/api/files", app=plain))
    app.router.routes.insert(0, Route("/export/report.json", plain, methods=["GET", "POST"]))
    app.router.routes.insert(0, WebSocketRoute("/ws", ws))
    client = TestClient(app)
    for path in ("/api/leak/x", "/api/plain", "/api/files/a", "/api/files"):
        r = client.get(path)
        assert r.status_code == 401 and "geheim" not in r.text, path
    assert client.post("/api/plain").status_code == 401
    assert client.post("/export/report.json").status_code == 405
    import pytest
    with pytest.raises(Exception):  # noqa: B017, PT011
        with client.websocket_connect("/ws") as conn:
            conn.receive_text()
    # und die Aufzählung bemerkt die fremden Routen
    kinds = {kind for *_, kind in all_routes(app.routes)}
    assert kinds - {"APIRoute"} == {"Route", "Mount", "WebSocketRoute"}
    found = {path for path, *_ in all_routes(app.routes)}
    assert "/api/leak/x" in found


def test_bare_api_path_is_json(client, admin):
    assert admin.get("/api").status_code == 404
    assert admin.get("/api").headers["content-type"].startswith("application/json")


def test_no_docs_routes(client):
    for path in ("/docs", "/redoc", "/openapi.json", "/api/openapi.json"):
        r = client.get(path)
        assert "openapi" not in r.text.lower() or r.status_code == 404


def test_unknown_api_path_is_json_404(admin):
    r = admin.get("/api/gibt-es-nicht")
    assert r.status_code == 404
    assert r.headers["content-type"].startswith("application/json")


def test_instances_and_settings_leak_no_secrets(admin):
    instances = admin.get("/api/instances")
    settings = admin.get("/api/settings")
    assert instances.status_code == 200 and settings.status_code == 200
    blob = instances.text + settings.text
    for secret in (ADMIN_PASSWORD, LLM_KEY, INSTANCE_PASSWORD, "nutzer-789"):
        assert secret not in blob
        assert json.dumps(secret)[1:-1] not in blob
    ids = [i["id"] for i in instances.json()]
    assert ids == ["wlo-prod", "wlo-staging", "eigene"]
    assert instances.json()[2]["has_credentials"] is True
    assert settings.json()["llm"] == {
        "base_url": "https://api.openai.com/v1", "model": "gpt-6-luna", "api_key_set": True}
