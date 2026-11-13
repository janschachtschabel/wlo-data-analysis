import json

import pytest
from fastapi.testclient import TestClient

from wda.main import create_app

from .conftest import ADMIN_PASSWORD
from .esdata import DataRepo

TAX = "ccm:taxonid"
QUERY = {"instance": "wlo-prod", "rows": {"property": TAX}}
FILTER = {"filters": [{"property": "ccm:educationalcontext", "values": ["Hochschule"]}]}


@pytest.fixture
def repo():
    return DataRepo()


@pytest.fixture
def api(settings, repo):
    with TestClient(create_app(settings, transport=repo.transport())) as client:
        assert client.post("/api/auth/login",
                           json={"password": ADMIN_PASSWORD}).status_code == 204
        yield client


def make(api, kind, name, spec, **extra):
    r = api.post("/api/definitions", json={"kind": kind, "name": name, "spec": spec, **extra})
    assert r.status_code == 201, r.text
    return r.json()


def tile(tid, **source):
    return {"id": tid, "title": tid, "layout": {"x": 0, "y": 0, "w": 6, "h": 4}, **source}


def test_groups_crud(api):
    assert api.get("/api/groups").json() == []
    a = api.post("/api/groups", json={"name": "Berichte"})
    assert a.status_code == 201 and a.json()["name"] == "Berichte"
    b = api.post("/api/groups", json={"name": "Qualität"}).json()
    r = api.put(f"/api/groups/{b['id']}", json={"position": 0, "name": "QS"})
    assert r.status_code == 200 and r.json()["position"] == 0
    assert [g["name"] for g in api.get("/api/groups").json()] == ["QS", "Berichte"]
    assert api.delete(f"/api/groups/{b['id']}").status_code == 204
    assert api.delete(f"/api/groups/{b['id']}").status_code == 404
    assert api.put("/api/groups/gibt-es-nicht", json={"name": "x"}).status_code == 404
    assert api.post("/api/groups", json={"name": " "}).status_code == 422
    assert api.post("/api/groups", json={}).status_code == 422


def test_definition_crud_and_listing(api):
    g = api.post("/api/groups", json={"name": "Berichte"}).json()
    f = make(api, "filter", "Hochschule", FILTER)
    m = make(api, "metric", "Fächer", {"query": QUERY, "filter_ids": [f["id"]]},
             group_id=g["id"], description="nach Fach")
    assert set(m) == {"id", "kind", "name", "description", "group_id", "spec", "version",
                      "created_at", "updated_at"}
    got = api.get(f"/api/definitions/{f['id']}").json()
    assert got["used_by"] == [{"id": m["id"], "kind": "metric", "name": "Fächer"}]
    listed = api.get("/api/definitions").json()
    assert [d["name"] for d in listed] == ["Fächer", "Hochschule"] and "spec" not in listed[0]
    assert [d["id"] for d in api.get("/api/definitions", params={"kind": "metric"}).json()] == [
        m["id"]]
    assert [d["id"] for d in api.get("/api/definitions", params={"group": ""}).json()] == [
        f["id"]]
    assert [d["id"] for d in api.get("/api/definitions", params={"q": "FACH"}).json()] == [
        m["id"]]
    assert api.get("/api/definitions", params={"kind": "bericht"}).status_code == 422

    r = api.put(f"/api/definitions/{m['id']}", json={"version": 1, "name": "Fächer neu"})
    assert r.status_code == 200 and r.json()["version"] == 2
    assert r.json()["group_id"] == g["id"] and r.json()["description"] == "nach Fach"
    stale = api.put(f"/api/definitions/{m['id']}", json={"version": 1, "name": "zu spät"})
    assert stale.status_code == 409 and stale.json()["kind"] == "conflict"
    r = api.put(f"/api/definitions/{m['id']}", json={"version": 2, "group_id": None})
    assert r.json()["group_id"] is None and r.json()["name"] == "Fächer neu"
    assert api.put(f"/api/definitions/{m['id']}", json={"name": "x"}).status_code == 422

    used = api.delete(f"/api/definitions/{f['id']}")
    assert used.status_code == 409 and used.json()["used_by"][0]["id"] == m["id"]
    copy = api.post(f"/api/definitions/{m['id']}/duplicate")
    assert copy.status_code == 201 and copy.json()["name"] == "Fächer neu (Kopie)"
    assert api.delete(f"/api/definitions/{copy.json()['id']}").status_code == 204
    assert api.delete(f"/api/definitions/{m['id']}").status_code == 204
    assert api.delete(f"/api/definitions/{f['id']}").status_code == 204
    for method, path in (("get", "/api/definitions/weg-weg"),
                         ("delete", "/api/definitions/weg-weg"),
                         ("post", "/api/definitions/weg-weg/duplicate"),
                         ("post", "/api/definitions/weg-weg/run"),
                         ("get", "/api/definitions/weg-weg/runs"),
                         ("get", "/api/runs/weg-weg"), ("delete", "/api/runs/weg-weg")):
        r = getattr(api, method)(path)
        assert r.status_code == 404 and r.json()["kind"] == "not_found", path


def test_error_bodies_have_one_shape_and_echo_nothing(api):
    bad = [
        api.post("/api/definitions", json={"kind": "metric", "name": "x",
                                           "spec": {"query": {"instance": "t", "x": 1}}}),
        api.post("/api/definitions", json={"kind": "metric", "name": "x"}),
        api.post("/api/definitions", json={"kind": "metric", "name": "streng-geheim" * 400,
                                           "spec": {}}),
        api.post("/api/definitions", content=b"{kaputt", headers={
            "content-type": "application/json"}),
        api.post("/api/definitions/import", content=b"{kaputt"),
        api.post("/api/definitions/import", json={"format": "fremd"}),
        api.post("/api/definitions/import?mode=merge", json={}),
        api.post("/api/query", json={"instance": "wlo-prod", "geheimnis": "streng-geheim"}),
        api.get("/api/gibt-es-nicht"),
    ]
    for r in bad:
        body = r.json()
        assert r.status_code in (404, 422), r.text
        assert isinstance(body["detail"], str) and body["detail"]
        assert isinstance(body["kind"], str)
        assert "streng-geheim" not in r.text
    assert bad[0].json()["kind"] == "invalid_definition"
    assert bad[1].json()["kind"] == "invalid_request"


def test_import_export_round_trip_and_size_limit(api):
    f = make(api, "filter", "Hochschule", FILTER)
    m = make(api, "metric", "Fächer", {"query": QUERY, "filter_ids": [f["id"]]})
    make(api, "metric", "Allein", {"query": QUERY})
    part = api.post("/api/definitions/export", json={"ids": [m["id"]]}).json()
    assert [d["name"] for d in part["definitions"]] == ["Hochschule", "Fächer"]
    everything = api.post("/api/definitions/export", json={}).json()
    assert len(everything["definitions"]) == 3
    r = api.post("/api/definitions/import", json=everything)
    assert r.status_code == 200
    assert {d["action"] for d in r.json()["definitions"]} == {"skipped"}
    r = api.post("/api/definitions/import?mode=copy", json=part)
    assert {d["action"] for d in r.json()["definitions"]} == {"created"}
    assert len(api.get("/api/definitions").json()) == 5
    assert api.post("/api/definitions/export", json={"ids": ["weg-weg"]}).status_code == 404
    huge = json.dumps({"format": "wda-definitions", "version": 1, "definitions": [],
                       "x": "y" * (5 * 1024 * 1024)})
    r = api.post("/api/definitions/import", content=huge.encode())
    assert r.status_code == 413 and r.json()["kind"] == "too_large"
    assert len(api.get("/api/definitions").json()) == 5


def test_run_routes(api, repo):
    f = make(api, "filter", "Hochschule", FILTER)
    m = make(api, "metric", "Fächer", {"query": QUERY, "filter_ids": [f["id"]]})
    d = make(api, "dashboard", "Überblick", {"tiles": [
        tile("fach", metric_id=m["id"]),
        tile("kaputt", query={"instance": "wlo-prod", "filters": [
            {"property": TAX, "values": ["Mathematk"]}]})]})
    r = api.post(f"/api/definitions/{m['id']}/run")
    assert r.status_code == 200, r.text
    run = r.json()
    assert run["status"] == "ok" and run["results"][0]["result"]["columns"][0]["total"] == (
        repo.brute(repo.has("ccm:educationalcontext",
                            "http://w3id.org/openeduhub/vocabs/educationalContext/hochschule")))
    assert set(run) == {"id", "definition_id", "kind", "name", "definition_version", "spec",
                        "status", "started_at", "finished_at", "saved", "results"}
    assert set(run["results"][0]) == {"key", "title", "view", "query", "result", "error"}
    assert api.get(f"/api/runs/{run['id']}").json() == run

    partial = api.post(f"/api/definitions/{d['id']}/run", json={"refresh": True}).json()
    assert partial["status"] == "partial"
    assert partial["results"][1]["error"]["kind"] == "unresolved_value"
    dry = api.post(f"/api/definitions/{d['id']}/run", json={"save": False}).json()
    assert dry["saved"] is False and api.get(f"/api/runs/{dry['id']}").status_code == 404
    other = api.post(f"/api/definitions/{m['id']}/run", json={"instance": "wlo-staging"})
    assert other.json()["results"][0]["query"]["instance"] == "wlo-staging"

    listed = api.get(f"/api/definitions/{m['id']}/runs").json()
    assert [x["id"] for x in listed] == [other.json()["id"], run["id"]]
    assert "results" not in listed[0]
    everything = api.get("/api/runs").json()
    assert len(everything) == 3
    page = api.get("/api/runs", params={"limit": 1}).json()
    rest = api.get("/api/runs", params={"limit": 5, "before": page[0]["seq"]}).json()
    assert len(page) == 1 and len(rest) == 2
    assert api.post(f"/api/definitions/{f['id']}/run").status_code == 422
    assert api.post(f"/api/definitions/{m['id']}/run", json={"x": 1}).status_code == 422
    assert api.delete(f"/api/runs/{run['id']}").status_code == 204
    assert api.get(f"/api/runs/{run['id']}").status_code == 404
    # Löschen der Definition lässt ihre Läufe stehen
    assert api.delete(f"/api/definitions/{d['id']}").status_code == 204
    assert api.get(f"/api/runs/{partial['id']}").json()["definition_id"] is None


def test_database_file_is_never_served(api, settings):
    make(api, "metric", "Fächer", {"query": QUERY})
    assert (settings.data_dir / "store.sqlite3").exists()
    for path in ("/store.sqlite3", "/data/store.sqlite3", "/%2e%2e/store.sqlite3",
                 "/api/store.sqlite3"):
        r = api.get(path)
        assert b"SQLite format" not in r.content


def test_unusable_data_dir_aborts_the_start(settings, repo, tmp_path):
    from wda.config import ConfigError
    blocker = tmp_path / "datei"
    blocker.write_text("x")
    settings.data_dir = blocker / "unterordner"
    with pytest.raises(ConfigError, match="lässt sich nicht öffnen"):
        with TestClient(create_app(settings, transport=repo.transport())):
            pass


def test_review_findings_of_package_4(api):
    m = make(api, "metric", "M", {"query": QUERY})
    # Tippfehler bei der Instanz: abgelehnt, kein gespeicherter Fehl-Lauf
    r = api.post(f"/api/definitions/{m['id']}/run", json={"instance": "tippfehler"})
    assert r.status_code == 422 and api.get("/api/runs").json() == []
    # Zeichen, die die Datenbank nicht speichern kann: Meldung statt 500, nichts importiert
    for name in ("x\\ud800", "nul\\u0000name"):
        raw = ('{"format":"wda-definitions","version":1,"definitions":[{"id":"abcdef","kind":'
               '"metric","name":"NAME","spec":{"query":{"instance":"wlo-prod"}}}]}'
               ).replace("NAME", name)
        r = api.post("/api/definitions/import", content=raw.encode())
        assert r.status_code == 422 and "Eintrag 1" in r.json()["detail"], r.text
    raw = ('{"format":"wda-definitions","version":1,"definitions":[{"id":"abcdef","kind":'
           '"template","name":"x","spec":{"title":"T","sections":[{"id":"a","type":"text",'
           '"markdown":"\\ud800"}]}}]}')
    assert api.post("/api/definitions/import", content=raw.encode()).status_code == 422
    assert len(api.get("/api/definitions").json()) == 1
    # Fehlermeldungen wiederholen keine beliebig große Eingabe
    r = api.post("/api/definitions/import", json={"format": "wda-definitions",
                                                  "version": "v" * 100000})
    assert r.status_code == 422 and len(r.content) < 500
    assert len(api.get("/api/definitions/" + "x" * 5000).content) < 500
    # Läufe ohne Definition sind gezielt abrufbar
    run = api.post(f"/api/definitions/{m['id']}/run").json()
    assert [x["id"] for x in api.get("/api/runs", params={"definition": m["id"]}).json()] == [
        run["id"]]
    assert api.get("/api/runs", params={"definition": "none"}).json() == []
    api.delete(f"/api/definitions/{m['id']}")
    assert [x["id"] for x in api.get("/api/runs", params={"definition": "none"}).json()] == [
        run["id"]]
    # zu große Anfragen enden vor dem Einlesen, auch ohne Login
    api.cookies.clear()
    r = api.post("/api/auth/login", content=b"x" * (7 * 1024 * 1024))
    assert r.status_code == 413 and r.json()["kind"] == "too_large"
