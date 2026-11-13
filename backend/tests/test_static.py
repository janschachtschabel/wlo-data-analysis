import pytest

MARKER = "ROHDATEN-DUERFEN-NIE-RAUS"


def test_root_serves_placeholder(client):
    r = client.get("/")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert "WLO-Daten-Dashboard" in r.text


def test_spa_fallback(client):
    r = client.get("/dashboards/42")
    assert r.status_code == 200
    assert "WLO-Daten-Dashboard" in r.text


@pytest.mark.parametrize("path", [
    "/data/roh.json", "/%2e%2e/data/roh.json", "/static/../roh.json", "/roh.json",
    "/session.secret", "/static/%2e%2e/%2e%2e/roh.json", "/..%2froh.json",
])
def test_data_dir_never_served(client, admin, settings, path):
    (settings.data_dir / "roh.json").write_text(MARKER)
    for c in (client, admin):
        r = c.get(path)
        assert MARKER not in r.text
        secret = (settings.data_dir / "session.secret").read_text()
        assert secret not in r.text


def test_security_headers(client):
    r = client.get("/")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "DENY"
    assert r.headers["referrer-policy"] == "no-referrer"
    api = client.get("/api/health")
    assert api.headers["cache-control"] == "no-store"
