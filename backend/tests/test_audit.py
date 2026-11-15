"""Befunde aus dem Abschluss-Audit: Herkunft schreibender Anfragen, globale Anmelde-Drossel,
Fehler der Ablage, Aufräumen verwaister Läufe, was der Lesezugang von Instanzen sieht."""
import sqlite3

import pytest
from fastapi.testclient import TestClient

from wda.config import Settings
from wda.main import create_app

from .conftest import ADMIN_PASSWORD
from .esdata import DataRepo
from .test_definition_routes import QUERY, make

VIEWER = "nur-lesen-4711"


def _app(tmp_path, **more):
    more.setdefault("cookie_secure", False)
    return create_app(Settings(admin_password=ADMIN_PASSWORD, data_dir=tmp_path,
                               scheduler=False, **more), transport=DataRepo().transport())


@pytest.fixture
def api(tmp_path):
    with TestClient(_app(tmp_path, viewer_password=VIEWER)) as client:
        assert client.post("/api/auth/login",
                           json={"password": ADMIN_PASSWORD}).status_code == 204
        yield client


@pytest.mark.parametrize("headers", [
    {"Sec-Fetch-Site": "cross-site"}, {"Sec-Fetch-Site": "same-site"},
    {"Origin": "https://nachbar.example.org"}, {"Origin": "null"}])
def test_writes_from_another_origin_are_refused(api, headers):
    m = make(api, "metric", "F", {"query": QUERY})
    out = api.post(f"/api/definitions/{m['id']}/run", headers=headers)
    assert out.status_code == 403 and out.json()["kind"] == "cross_origin"
    assert api.post("/api/auth/logout", headers=headers).status_code == 403
    assert api.get("/api/runs", headers=headers).status_code == 200  # lesen bleibt frei


@pytest.mark.parametrize("headers", [
    {}, {"Sec-Fetch-Site": "same-origin"}, {"Sec-Fetch-Site": "none"},
    {"Origin": "http://testserver"},
    {"Sec-Fetch-Site": "same-origin", "Origin": "https://hinter-dem-proxy.example.org"}])
def test_writes_from_the_app_itself_pass(api, headers):
    m = make(api, "metric", "F", {"query": QUERY})
    assert api.post(f"/api/definitions/{m['id']}/run", headers=headers).status_code < 300


def test_login_is_throttled_across_addresses(tmp_path, monkeypatch):
    from wda.api import auth
    addresses = iter(f"9.9.9.{n}" for n in range(100))
    monkeypatch.setattr(auth, "_client_key", lambda request: next(addresses))
    with TestClient(_app(tmp_path)) as client:
        codes = [client.post("/api/auth/login", json={"password": "falsch"}).status_code
                 for n in range(40)]
        assert codes.count(401) == 30
        assert codes[0] == 401 and codes[-1] == 429
        # auch das richtige Passwort wartet dann: Raten lohnt nicht
        assert client.post("/api/auth/login",
                           json={"password": ADMIN_PASSWORD}).status_code == 429


def test_storage_errors_answer_plainly(api, monkeypatch):
    def broken(*_args, **_kwargs):
        raise sqlite3.OperationalError("database or disk is full")

    monkeypatch.setattr(api.app.state.services.db, "read", broken)
    out = api.get("/api/runs")
    assert out.status_code == 503 and out.json()["kind"] == "storage_unavailable"
    assert "disk" not in out.text


def test_runs_of_deleted_definitions_are_pruned(tmp_path):
    with TestClient(_app(tmp_path, runs_keep=2)) as client:
        client.post("/api/auth/login", json={"password": ADMIN_PASSWORD})
        for name in "abc":
            m = make(client, "metric", name, {"query": QUERY})
            for _ in range(2):
                assert client.post(f"/api/definitions/{m['id']}/run").status_code < 300
            assert client.delete(f"/api/definitions/{m['id']}").status_code == 204
        keep = make(client, "metric", "bleibt", {"query": QUERY})
        client.post(f"/api/definitions/{keep['id']}/run")
        orphans = [r for r in client.get("/api/runs?limit=50").json()
                   if r["definition_id"] is None]
        assert len(orphans) == 2


def test_viewer_sees_instances_by_name_only(api):
    full = api.get("/api/instances").json()
    assert "base_url" in full[0]
    api.cookies.clear()
    api.post("/api/auth/login", json={"password": VIEWER})
    seen = api.get("/api/instances").json()
    assert [i["id"] for i in seen] == [i["id"] for i in full]
    assert all(set(i) <= {"id", "name", "default"} for i in seen), seen[0]


def test_hsts_only_with_secure_cookies(tmp_path):
    with TestClient(_app(tmp_path / "a", cookie_secure=True)) as client:
        assert "max-age=" in client.get("/api/health").headers["Strict-Transport-Security"]
    with TestClient(_app(tmp_path / "b")) as client:
        assert "Strict-Transport-Security" not in client.get("/api/health").headers


def test_a_damaged_database_stops_the_start_with_a_sentence(tmp_path, monkeypatch, capsys):
    from wda import __main__ as entry
    (tmp_path / "store.sqlite3").write_bytes(b"kein sqlite " * 500)
    monkeypatch.setenv("WDA_ADMIN_PASSWORD", ADMIN_PASSWORD)
    monkeypatch.setenv("WDA_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(entry.uvicorn, "run", lambda *a, **k: pytest.fail("gestartet"))
    assert entry.main() == 2
    assert "Start abgebrochen" in capsys.readouterr().err
