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


@pytest.mark.parametrize("raw_path", [
    b"/static/../roh.json", b"/../roh.json", b"/%2e%2e/roh.json", b"//roh.json",
    b"/static/..%2f..%2fsession.secret",
])
def test_raw_traversal_paths(app, settings, raw_path):
    """Pfade unverändert an die App geben; ein HTTP-Client würde ``..`` vorher auflösen."""
    import asyncio
    from urllib.parse import unquote

    (settings.data_dir / "roh.json").write_text(MARKER)
    body = bytearray()

    sent_request = False

    async def receive():
        nonlocal sent_request
        if sent_request:
            return {"type": "http.disconnect"}
        sent_request = True
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        if message["type"] == "http.response.body":
            body.extend(message.get("body", b""))

    scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "GET",
        "scheme": "http", "path": unquote(raw_path.decode()), "raw_path": raw_path,
        "query_string": b"", "headers": [], "client": ("203.0.113.9", 1234),
        "server": ("testserver", 80), "root_path": "",
    }
    asyncio.run(app(scope, receive, send))
    text = body.decode("utf-8", "replace")
    assert MARKER not in text
    assert (settings.data_dir / "session.secret").read_text() not in text
