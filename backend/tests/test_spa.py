import pytest
from fastapi.testclient import TestClient

from wda.main import create_app


@pytest.fixture
def site(settings, tmp_path):
    static = tmp_path / "site"
    (static / "media").mkdir(parents=True)
    (static / "index.html").write_text("<!doctype html><title>Oberfläche</title>", encoding="utf-8")
    (static / "main-AB12CD34.js").write_text("console.log(1)")
    (static / "styles-XY98ZW76.css").write_text("body{}")
    (static / "chunk-C-ffZ7cx.js").write_text("console.log(2)")  # Hash mit Bindestrich
    (static / "favicon.ico").write_bytes(b"\x00\x01")
    (static / "media" / "bild.svg").write_text("<svg/>")
    (static / ".geheim").write_text("VERSTECKT")
    (tmp_path / "ausserhalb.js").write_text("ROH")
    return TestClient(create_app(settings, static_dir=static))


def test_files_of_the_build_are_served_with_their_type_and_cache(site):
    js = site.get("/main-AB12CD34.js")
    assert js.status_code == 200 and js.text == "console.log(1)"
    assert "javascript" in js.headers["content-type"]
    assert js.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert site.get("/styles-XY98ZW76.css").headers["content-type"].startswith("text/css")
    assert "immutable" in site.get("/chunk-C-ffZ7cx.js").headers["cache-control"]
    assert site.get("/media/bild.svg").status_code == 200
    # nicht gehashte Dateien werden jedes Mal geprüft
    assert site.get("/favicon.ico").headers["cache-control"] == "no-cache"


def test_routes_of_the_app_get_the_index_missing_files_do_not(site):
    for path in ("/", "/dashboard/42", "/lauf/abc", "/index.html"):
        r = site.get(path)
        assert r.status_code == 200 and "Oberfläche" in r.text
        assert r.headers["cache-control"] == "no-cache"
    # eine fehlende Datei ist kein HTML: sonst läse der Browser index.html als Skript
    for path in ("/main-ALT.js", "/media/fehlt.png", "/x.map"):
        assert site.get(path).status_code == 404


@pytest.mark.parametrize("path", [
    "/.geheim", "/../ausserhalb.js", "/media/../../ausserhalb.js", "/%2e%2e/ausserhalb.js",
    "/media/..%2f..%2fausserhalb.js", "//ausserhalb.js", "/store.sqlite3", "/session.secret",
])
def test_nothing_outside_the_build_is_reachable(site, path):
    r = site.get(path)
    assert "ROH" not in r.text and "VERSTECKT" not in r.text
    assert r.status_code == 404 or "Oberfläche" in r.text


def test_the_app_shell_is_locked_down(site):
    csp = site.get("/").headers["content-security-policy"]
    for part in ("default-src 'self'", "script-src 'self'", "object-src 'none'",
                 "frame-ancestors 'none'", "base-uri 'self'", "form-action 'self'"):
        assert part in csp
    assert "unsafe-eval" not in csp and "script-src 'self' 'unsafe-inline'" not in csp
    assert site.post("/").status_code == 405
