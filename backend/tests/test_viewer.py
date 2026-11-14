"""Lesezugang: ein zweites Passwort, mit dem sich abgelegte Läufe und Berichte ansehen lassen.
Ändern, ausführen, Einstellungen und der Assistent bleiben dem Admin."""
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from wda.config import Settings
from wda.deps import VIEWER_MAY
from wda.main import PUBLIC_API_PATHS, create_app

from .conftest import ADMIN_PASSWORD
from .esdata import DataRepo
from .test_definition_routes import QUERY, make
from .test_routes_guard import all_routes, is_api

VIEWER_PASSWORD = "nur-lesen-4711"  # noqa: S105


@pytest.fixture
def app(tmp_path):
    settings = Settings(admin_password=ADMIN_PASSWORD, viewer_password=VIEWER_PASSWORD,
                        cookie_secure=False, data_dir=tmp_path, scheduler=False)
    return create_app(settings, transport=DataRepo().transport())


@pytest.fixture
def viewer(app):
    with TestClient(app) as admin:
        assert admin.post("/api/auth/login",
                          json={"password": ADMIN_PASSWORD}).status_code == 204
        assert admin.get("/api/auth/status").json() == {"authenticated": True, "role": "admin"}
        m = make(admin, "metric", "Fächer", {"query": QUERY})
        run = admin.post(f"/api/definitions/{m['id']}/run").json()
        admin.cookies.clear()
        assert admin.post("/api/auth/login",
                          json={"password": VIEWER_PASSWORD}).status_code == 204
        admin.ids = {"def_id": m["id"], "run_id": run["id"]}
        yield admin


def test_viewer_sees_stored_runs_and_reports(viewer):
    ids = viewer.ids
    assert viewer.get("/api/auth/status").json() == {"authenticated": True, "role": "viewer"}
    for path in ("/api/runs", f"/api/runs/{ids['run_id']}", f"/api/runs/{ids['run_id']}/report",
                 f"/api/runs/{ids['run_id']}/export?format=csv", "/api/highlights",
                 f"/api/definitions/{ids['def_id']}/runs",
                 f"/api/definitions/{ids['def_id']}/history",
                 f"/api/definitions/{ids['def_id']}/history/keys",
                 f"/api/definitions/{ids['def_id']}/trends", "/api/settings", "/api/instances",
                 f"/api/definitions/{ids['def_id']}"):
        assert viewer.get(path).status_code == 200, path
    # die Einstellungen des Sprachmodells sieht nur der Admin
    assert viewer.get("/api/settings").json()["llm"] == {"ready": False}
    # Instanzen erscheinen ohne Zugangsdaten
    assert "password" not in viewer.get("/api/instances").text


def test_everything_else_is_refused(app, viewer):
    """Erlaubt ist nur, was in der Liste steht; jede andere Route antwortet mit 403."""
    allowed = refused = 0
    for path, methods, _, _ in all_routes(app.routes):
        if not is_api(path) or path in PUBLIC_API_PATHS:
            continue
        for method in methods - {"HEAD", "OPTIONS"}:
            real = (path.replace("{run_id}", viewer.ids["run_id"])
                    .replace("{def_id}", viewer.ids["def_id"]))
            status = viewer.request(method, real).status_code
            if method == "GET" and path in VIEWER_MAY:
                assert status == 200, f"{method} {path}: {status}"
                allowed += 1
            else:
                assert status == 403, f"{method} {path} geht mit dem Lesezugang ({status})"
                refused += 1
    assert allowed == len(VIEWER_MAY) and refused > 40
    out = viewer.post(f"/api/definitions/{viewer.ids['def_id']}/run")
    assert out.json()["kind"] == "forbidden"
    assert viewer.delete(f"/api/runs/{viewer.ids['run_id']}").status_code == 403
    assert viewer.get(f"/api/runs/{viewer.ids['run_id']}").status_code == 200  # noch da


def test_without_the_variable_there_is_only_the_admin(tmp_path):
    settings = Settings(admin_password=ADMIN_PASSWORD, cookie_secure=False, data_dir=tmp_path,
                        scheduler=False)
    with TestClient(create_app(settings, transport=DataRepo().transport())) as client:
        assert client.post("/api/auth/login",
                           json={"password": VIEWER_PASSWORD}).status_code == 401
        assert client.post("/api/auth/login", json={"password": ""}).status_code == 401
    # dasselbe Passwort für beide Zugänge wäre ein Admin-Zugang für alle: abgelehnt
    with pytest.raises(ValidationError):
        Settings(admin_password=ADMIN_PASSWORD, viewer_password=ADMIN_PASSWORD,
                 data_dir=tmp_path)
    with pytest.raises(ValidationError):
        Settings(admin_password=ADMIN_PASSWORD, viewer_password="   ", data_dir=tmp_path)
