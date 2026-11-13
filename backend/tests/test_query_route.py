import pytest
from fastapi.testclient import TestClient

from wda.main import create_app

from .conftest import ADMIN_PASSWORD
from .esdata import DISC, DataRepo

TAX = "ccm:taxonid"


@pytest.fixture
def repo():
    return DataRepo()


@pytest.fixture
def api(settings, repo):
    client = TestClient(create_app(settings, transport=repo.transport()))
    assert client.post("/api/auth/login", json={"password": ADMIN_PASSWORD}).status_code == 204
    return client


def test_query_success(api, repo):
    r = api.post("/api/query", json={
        "instance": "wlo-prod",
        "filters": [{"property": "ccm:educationalcontext", "values": ["Hochschule"]}],
        "rows": {"property": TAX, "missing": True},
        "series": [{"id": "gesamt", "label": "Gesamt"},
                   {"id": "oer", "label": "OER", "filters": [
                       {"property": "license", "values": ["OPEN", "CC_BY_OPEN"]}]},
                   {"id": "anteil", "label": "Anteil", "formula": "round(oer/gesamt*100, 1)"}],
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body) == {"instance", "columns", "rows", "provenance"}
    assert [c["id"] for c in body["columns"]] == ["gesamt", "oer", "anteil"]
    assert set(body["rows"][0]) == {"key", "label", "kind", "values"}
    assert body["rows"][-1]["kind"] == "missing"
    math = next(x for x in body["rows"] if x["key"] == DISC + "380")
    assert math["label"] == "Mathematik" and math["values"][0] >= math["values"][1]
    assert "nodes" not in r.text


def test_query_errors(api, repo):
    r = api.post("/api/query", json={"instance": "wlo-prod",
                                     "series": [{"id": "a", "label": "A", "formula": "b"}]})
    assert r.status_code == 422
    assert r.json()["kind"] == "invalid_query" and "unbekannte Spalten" in r.json()["detail"]
    assert api.post("/api/query", json={"instance": "gibt-es-nicht"}).status_code == 404
    assert api.post("/api/query", json={"instance": "wlo-prod", "unbekannt": 1}).status_code == 422
    assert api.post("/api/query", json={}).status_code == 422
    r = api.post("/api/query", json={"instance": "wlo-prod", "rows": {"property": "cclom:title"}})
    assert r.status_code == 422 and r.json()["kind"] == "invalid_query"
    r = api.post("/api/query", json={"instance": "wlo-prod",
                                     "rows": {"property": TAX, "include": ["Mathe"]}})
    assert r.status_code == 422 and r.json()["suggestions"][0]["caption"] == "Mathematik"
    r = api.post("/api/query", json={"instance": "wlo-prod", "filters": [
        {"property": "ccm:unbekannt", "values": ["x"]}]})
    assert r.status_code == 422 and r.json()["kind"] == "unknown_property"
    r = api.post("/api/query", json={"instance": "wlo-prod", "filters": [
        {"property": "ccm:commonlicense_key", "values": ["CC_BY"]}]})
    assert r.status_code == 422 and "mitzählen" in r.json()["detail"]
    r = api.post("/api/query", json={"instance": "wlo-prod", "series": [
        {"id": "a", "label": "A"}, {"id": "b", "label": "B", "formula": "a * " + "9" * 400}]})
    assert r.status_code == 200 and r.json()["columns"][1]["total"] is None


def test_query_requires_login(settings, repo):
    client = TestClient(create_app(settings, transport=repo.transport()))
    assert client.post("/api/query", json={"instance": "wlo-prod"}).status_code == 401
    assert repo.requests == []
