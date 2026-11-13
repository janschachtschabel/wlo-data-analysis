import os
import stat

from fastapi.testclient import TestClient
from pydantic import SecretStr

from wda.config import Settings
from wda.main import create_app
from wda.security import SessionSigner, resolve_session_secret, verify_password

from .conftest import ADMIN_PASSWORD


def test_health_is_public(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_login_wrong_password(client):
    r = client.post("/api/auth/login", json={"password": "falsch"})
    assert r.status_code == 401
    assert "set-cookie" not in r.headers
    assert client.get("/api/auth/status").json() == {"authenticated": False}


def test_login_logout_cycle(client):
    r = client.post("/api/auth/login", json={"password": ADMIN_PASSWORD})
    assert r.status_code == 204
    cookie = r.headers["set-cookie"].lower()
    assert "wda_session=" in cookie
    assert "httponly" in cookie and "samesite=strict" in cookie and "path=/" in cookie
    assert "secure" not in cookie  # cookie_secure=False in der Testkonfiguration
    assert client.get("/api/auth/status").json() == {"authenticated": True}
    assert client.get("/api/instances").status_code == 200
    assert client.post("/api/auth/logout").status_code == 204
    assert client.get("/api/auth/status").json() == {"authenticated": False}
    assert client.get("/api/instances").status_code == 401


def test_secure_cookie_by_default(tmp_path):
    app = create_app(Settings(admin_password="a", data_dir=tmp_path))
    r = TestClient(app, base_url="https://testserver").post(
        "/api/auth/login", json={"password": "a"})
    assert "secure" in r.headers["set-cookie"].lower()


def test_tampered_cookie(client):
    client.cookies.set("wda_session", "abc.def.ghi")
    assert client.get("/api/auth/status").json() == {"authenticated": False}
    r = client.get("/api/instances")
    assert r.status_code == 401
    assert r.json() == {"detail": "Nicht angemeldet"}


def test_cookie_from_other_secret_rejected(client):
    client.cookies.set("wda_session", SessionSigner("anderes-geheimnis", 3600).issue())
    assert client.get("/api/instances").status_code == 401


def test_expired_token():
    signer = SessionSigner("s", max_age=-1)
    assert signer.valid(signer.issue()) is False
    assert SessionSigner("s", 60).valid(None) is False
    assert SessionSigner("s", 60).valid("") is False


def test_session_expires_over_http(admin, settings, monkeypatch):
    import itsdangerous.timed as timed

    assert admin.get("/api/instances").status_code == 200
    real = timed.time.time
    monkeypatch.setattr(timed.time, "time", lambda: real() + settings.session_max_age - 60)
    assert admin.get("/api/instances").status_code == 200
    monkeypatch.setattr(timed.time, "time", lambda: real() + settings.session_max_age + 60)
    assert admin.get("/api/instances").status_code == 401
    assert admin.get("/api/auth/status").json() == {"authenticated": False}


def test_password_change_invalidates_sessions(admin, settings):
    cookie = admin.cookies.get("wda_session")
    changed = settings.model_copy(update={"admin_password": SecretStr("neues passwort")})
    other = TestClient(create_app(changed))
    other.cookies.set("wda_session", cookie)
    assert other.get("/api/auth/status").json() == {"authenticated": False}
    same = TestClient(create_app(settings))
    same.cookies.set("wda_session", cookie)
    assert same.get("/api/auth/status").json() == {"authenticated": True}


def test_lone_surrogate_password_is_plain_failure(client):
    r = client.post("/api/auth/login", content=b'{"password":"\\ud800"}',
                    headers={"content-type": "application/json"})
    assert r.status_code == 401
    for _ in range(4):
        client.post("/api/auth/login", json={"password": "falsch"})
    assert client.post("/api/auth/login", json={"password": "falsch"}).status_code == 429


def test_unwritable_data_dir_logs_warning(tmp_path, caplog):
    (tmp_path / "datei").write_text("x")
    s = Settings(admin_password="a", data_dir=tmp_path / "datei")
    with caplog.at_level("WARNING", logger="wda.security"):
        secret = resolve_session_secret(s)
    assert "Session-Geheimnis" in caplog.text
    assert secret not in caplog.text


def test_malformed_login_body(client):
    assert client.post("/api/auth/login", json={}).status_code == 422
    assert client.post("/api/auth/login", content=b"kein json").status_code == 422
    assert client.post("/api/auth/login", json={"password": 5}).status_code == 422


def test_throttle_after_five_failures(client, clock):
    for _ in range(5):
        assert client.post("/api/auth/login", json={"password": "falsch"}).status_code == 401
    assert client.post("/api/auth/login", json={"password": "falsch"}).status_code == 429
    # auch das richtige Passwort wird in der Sperrzeit abgelehnt
    r = client.post("/api/auth/login", json={"password": ADMIN_PASSWORD})
    assert r.status_code == 429
    assert int(r.headers["retry-after"]) > 0
    clock.now += 61
    assert client.post("/api/auth/login", json={"password": ADMIN_PASSWORD}).status_code == 204


def test_successful_login_resets_failures(client):
    for _ in range(4):
        client.post("/api/auth/login", json={"password": "falsch"})
    assert client.post("/api/auth/login", json={"password": ADMIN_PASSWORD}).status_code == 204
    for _ in range(4):
        assert client.post("/api/auth/login", json={"password": "falsch"}).status_code == 401


def test_verify_password():
    assert verify_password("ä✓", "ä✓")
    assert not verify_password("a", "ä")
    assert not verify_password("", "x")


def test_session_secret_persisted(tmp_path):
    s = Settings(admin_password="a", data_dir=tmp_path)
    first = resolve_session_secret(s)
    assert first == resolve_session_secret(s)
    secret_file = tmp_path / "session.secret"
    if os.name == "posix":  # Windows kennt keine POSIX-Dateirechte
        assert stat.S_IMODE(secret_file.stat().st_mode) == 0o600
    assert len(first) >= 32


def test_session_secret_from_env_wins(tmp_path):
    s = Settings(admin_password="a", data_dir=tmp_path, session_secret="fest")
    assert resolve_session_secret(s) == "fest"
    assert not (tmp_path / "session.secret").exists()


def test_session_secret_unwritable_dir(tmp_path):
    s = Settings(admin_password="a", data_dir=tmp_path / "datei")
    (tmp_path / "datei").write_text("ich bin eine Datei")
    assert len(resolve_session_secret(s)) >= 32
