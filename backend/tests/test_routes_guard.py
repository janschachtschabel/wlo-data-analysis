import json

from fastapi.routing import APIRoute

from wda.main import PUBLIC_API_PATHS

from .conftest import ADMIN_PASSWORD, INSTANCE_PASSWORD, LLM_KEY


def test_public_paths_are_exactly_these():
    assert PUBLIC_API_PATHS == frozenset({
        "/api/health", "/api/auth/login", "/api/auth/status", "/api/auth/logout"})


def test_every_other_api_route_requires_admin(app, client):
    checked = 0
    for route in app.routes:
        if not isinstance(route, APIRoute) or not route.path.startswith("/api"):
            continue
        if route.path in PUBLIC_API_PATHS:
            continue
        for method in route.methods - {"HEAD", "OPTIONS"}:
            r = client.request(method, route.path)
            assert r.status_code == 401, f"{method} {route.path} ist ohne Login erreichbar"
            checked += 1
    assert checked >= 2


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
