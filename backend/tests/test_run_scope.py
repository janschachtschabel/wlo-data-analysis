"""Kontext eines Laufs: dieselbe Auswertung für den Gesamtbestand oder eine Quelle."""

import pytest
from fastapi.testclient import TestClient

from wda.main import create_app

from .conftest import ADMIN_PASSWORD
from .esdata import SOURCES, DataRepo

SRC = "ccm:replicationsource"
TAX = "ccm:taxonid"


@pytest.fixture
def api(settings):
    with TestClient(create_app(settings, transport=DataRepo().transport())) as client:
        assert client.post("/api/auth/login",
                           json={"password": ADMIN_PASSWORD}).status_code == 204
        yield client


def make(api, kind, name, spec):
    r = api.post("/api/definitions", json={"kind": kind, "name": name, "spec": spec})
    assert r.status_code == 201, r.text
    return r.json()


def tile(tid, query):
    return {"id": tid, "title": tid, "layout": {"x": 0, "y": 0, "w": 6, "h": 4}, "query": query}


def total(run, key="main"):
    result = next(r for r in run["results"] if r["key"] == key)
    assert result["error"] is None, result["error"]
    return result["result"]["columns"][0]["total"]


def test_scope_narrows_every_tile_and_is_not_saved(api):
    d = make(api, "dashboard", "D", {"tiles": [
        tile("a", {"instance": "wlo-prod", "rows": {"property": TAX}}),
        tile("b", {"instance": "wlo-prod", "rows": {"property": SRC}}),
    ]})
    scope = [{"property": SRC, "values": [{"value": SOURCES[0], "literal": True}]}]
    whole = api.post(f"/api/definitions/{d['id']}/run", json={"save": False}).json()
    part = api.post(f"/api/definitions/{d['id']}/run", json={"save": False, "scope": scope})
    assert part.status_code == 200, part.text
    part = part.json()
    assert 0 < total(part, "a") < total(whole, "a")
    assert total(part, "b") == total(part, "a")
    assert part["scope"] == scope and "scope" not in whole
    assert part["results"][0]["query"]["filters"][0]["property"] == SRC
    # die Definition selbst bleibt, wie sie ist
    assert "scope" not in api.get(f"/api/definitions/{d['id']}").json()["spec"]


def test_a_scoped_run_cannot_be_saved(api):
    m = make(api, "metric", "M", {"query": {"instance": "wlo-prod", "rows": {"property": TAX}}})
    scope = [{"property": SRC, "values": [{"value": SOURCES[0], "literal": True}]}]
    assert api.post(f"/api/definitions/{m['id']}/run", json={"scope": scope}).status_code == 422
    assert api.post(f"/api/definitions/{m['id']}/run",
                    json={"scope": scope, "wait": False}).status_code == 422
    too_many = [{"property": f"p{i}", "values": ["x"]} for i in range(4)]
    assert api.post(f"/api/definitions/{m['id']}/run",
                    json={"save": False, "scope": too_many}).status_code == 422


def test_a_tile_with_its_own_filter_on_the_field_says_so(api):
    d = make(api, "dashboard", "D", {"tiles": [
        tile("eigen", {"instance": "wlo-prod", "rows": {"property": TAX}, "filters": [
            {"property": SRC, "values": [{"value": SOURCES[1], "literal": True}]}]}),
        tile("frei", {"instance": "wlo-prod", "rows": {"property": TAX}}),
    ]})
    scope = [{"property": SRC, "values": [{"value": SOURCES[0], "literal": True}]}]
    run = api.post(f"/api/definitions/{d['id']}/run", json={"save": False, "scope": scope}).json()
    own = next(r for r in run["results"] if r["key"] == "eigen")
    assert own["error"]["kind"] == "invalid_query" and "Kontext" in own["error"]["detail"]
    assert total(run, "frei") > 0 and run["status"] == "partial"


def test_the_report_names_the_scope(api):
    m = make(api, "metric", "M", {"query": {"instance": "wlo-prod", "rows": {"property": TAX}}})
    scope = [{"property": SRC, "values": [{"value": SOURCES[0], "literal": True}]}]
    run = api.post(f"/api/definitions/{m['id']}/run",
                   json={"save": False, "report": True, "scope": scope}).json()
    assert run["report"]["scope"] == scope


def test_rows_on_the_scope_field_drop_missing_and_other(api):
    d = make(api, "dashboard", "D", {"tiles": [
        tile("je", {"instance": "wlo-prod", "rows": {
            "property": SRC, "top": 2, "missing": True, "other": True}})]})
    scope = [{"property": SRC, "values": [{"value": SOURCES[0], "literal": True}]}]
    run = api.post(f"/api/definitions/{d['id']}/run", json={"save": False, "scope": scope}).json()
    result = run["results"][0]
    assert result["error"] is None, result["error"]
    assert [r["key"] for r in result["result"]["rows"] if r["kind"] == "value"] == [SOURCES[0]]
    assert not [r for r in result["result"]["rows"] if r["kind"] in ("missing", "other")]


def test_the_builtin_dashboards_work_in_the_context_of_a_source(api):
    """Kacheln, die nach dem Feld des Kontexts aufschlüsseln, zeigen den gewählten Wert."""
    pub = "ccm:oeh_publisher_combined"
    first = api.post("/api/query", json={"instance": "wlo-prod", "rows": {
        "property": pub, "top": 1}}).json()["rows"][0]
    for bundle in ("datenqualitaet", "abdeckung", "datenprobleme", "quellen"):
        assert api.post(f"/api/builtin/{bundle}/install").status_code == 200
        base = api.post(f"/api/definitions/builtin-{bundle}/run", json={"save": False}).json()
        broken = {r["key"] for r in base["results"] if r["error"]}
        for prop, value in ((SRC, SOURCES[0]), (pub, first["key"])):
            scope = [{"property": prop, "values": [{"value": value, "literal": True}]}]
            run = api.post(f"/api/definitions/builtin-{bundle}/run",
                           json={"save": False, "scope": scope}).json()
            errors = {r["key"]: r["error"]["detail"] for r in run["results"]
                      if r["error"] and r["key"] not in broken}
            # allein die Größenklassen des Kontextfeldes entfallen, mit klarem Satz
            assert all("Entfällt im gewählten Kontext" in text for text in errors.values()), (
                bundle, prop, errors)
            if not broken:
                assert run["status"] == "ok", (bundle, prop)


def test_rows_on_the_scope_field_count_the_chosen_value(api):
    d = make(api, "dashboard", "D", {"tiles": [
        tile("je", {"instance": "wlo-prod", "rows": {"property": SRC, "top": 2}}),
        tile("alle", {"instance": "wlo-prod", "rows": {"property": TAX}})]})
    scope = [{"property": SRC, "values": [{"value": SOURCES[1], "literal": True}]}]
    run = api.post(f"/api/definitions/{d['id']}/run", json={"save": False, "scope": scope}).json()
    by = {r["key"]: r["result"] for r in run["results"]}
    rows = [r for r in by["je"]["rows"] if r["kind"] == "value"]
    assert [r["key"] for r in rows] == [SOURCES[1]]
    assert rows[0]["values"][0] == by["alle"]["columns"][0]["total"]
