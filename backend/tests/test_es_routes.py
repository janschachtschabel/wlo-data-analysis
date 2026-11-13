import httpx
import pytest
from fastapi.testclient import TestClient

from wda.main import create_app

from .conftest import ADMIN_PASSWORD
from .esfake import DISC, EC, HIDDEN, FakeRepo, error_response


@pytest.fixture
def repo():
    return FakeRepo()


@pytest.fixture
def api(settings, repo):
    client = TestClient(create_app(settings, transport=repo.transport()))
    assert client.post("/api/auth/login", json={"password": ADMIN_PASSWORD}).status_code == 204
    return client


def test_about(api, repo):
    r = api.get("/api/instances/wlo-prod/about")
    assert r.status_code == 200
    assert r.json() == {
        "instance": "wlo-prod", "reachable": True, "total": 318912,
        "version": {"repository": "11.0", "renderservice": "unknown", "major": 1, "minor": 1}}
    assert repo.requests[0].url.host == "redaktion.openeduhub.net"


def test_about_unreachable(api, repo):
    repo.script = [httpx.ConnectError("weg")] * 3
    r = api.get("/api/instances/wlo-staging/about")
    assert r.status_code == 200
    body = r.json()
    assert body["reachable"] is False
    assert body["error"]["kind"] == "unavailable"


def test_unknown_instance(api):
    for path in ("/about", "/fields", "/vocab?property=ccm:taxonid"):
        r = api.get("/api/instances/gibt-es-nicht" + path)
        assert r.status_code == 404, path
    assert api.post("/api/instances/gibt-es-nicht/aggregate", json={}).status_code == 404


def test_fields(api):
    r = api.get("/api/instances/wlo-prod/fields")
    assert r.status_code == 200
    assert r.json()[0] == {"property": "ccm:taxonid", "caption": "Fach",
                           "type": "multivalueTree", "value_count": 7}
    assert [f["property"] for f in r.json()] == [
        "ccm:taxonid", "ccm:educationalcontext", "ccm:commonlicense_key", "cclom:title"]


def test_vocab(api):
    r = api.get("/api/instances/wlo-prod/vocab", params={"property": "ccm:taxonid"})
    body = r.json()
    assert r.status_code == 200
    assert (body["property"], body["caption"], body["total"]) == ("ccm:taxonid", "Fach", 7)
    assert body["values"][1] == {"id": DISC + "380", "caption": "Mathematik", "parent": DISC}
    r = api.get("/api/instances/wlo-prod/vocab",
                params={"property": "ccm:taxonid", "q": "GRIECH", "limit": 1})
    body = r.json()
    assert body["total"] == 2 and len(body["values"]) == 1
    assert body["values"][0]["caption"] == "Griechisch"
    r = api.get("/api/instances/wlo-prod/vocab",
                params={"property": "ccm:taxonid", "q": DISC + "46"})
    assert [v["caption"] for v in r.json()["values"]] == ["Physik"]
    assert api.get("/api/instances/wlo-prod/vocab",
                   params={"property": "gibt:es:nicht"}).status_code == 404
    assert api.get("/api/instances/wlo-prod/vocab").status_code == 422
    assert api.get("/api/instances/wlo-prod/vocab",
                   params={"property": "ccm:taxonid", "limit": 0}).status_code == 422


def test_aggregate_with_labels(api, repo):
    r = api.post("/api/instances/wlo-prod/aggregate", json={
        "filters": [
            {"property": "ccm:educationalcontext", "values": ["Sekundarstufe I"]},
            {"property": "license", "values": ["OPEN", "CC_BY_OPEN"]},
        ],
        "facets": ["ccm:taxonid", "ccm:educationalcontext"],
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert repo.bodies()[0]["criteria"] == [
        {"property": "ccm:educationalcontext", "values": [EC + "sekundarstufe_1"]},
        {"property": "license", "values": ["CC_BY_OPEN", "OPEN"]},
    ]
    assert body["instance"] == "wlo-prod"
    assert body["total"] == 318912
    assert body["cached"] is False and isinstance(body["fetched_at"], float)
    assert [f["property"] for f in body["facets"]] == ["ccm:taxonid", "ccm:educationalcontext"]
    taxon = body["facets"][0]
    assert taxon["caption"] == "Fach" and taxon["truncated"] is False
    assert taxon["values"][0] == {"value": DISC + "380", "label": "Mathematik", "count": 26005}
    context = body["facets"][1]["values"]
    assert context[2] == {"value": "political", "label": "political", "count": 24}
    assert body["filters"][0] == {"property": "ccm:educationalcontext", "values": [
        {"value": EC + "sekundarstufe_1", "label": "Sekundarstufe I", "how": "label"}]}
    assert body["filters"][1]["values"][0]["how"] == "literal"
    again = api.post("/api/instances/wlo-prod/aggregate", json={
        "filters": [
            {"property": "license", "values": ["CC_BY_OPEN", "OPEN"]},
            {"property": "ccm:educationalcontext", "values": [EC + "sekundarstufe_1"]},
        ],
        "facets": ["ccm:educationalcontext", "ccm:taxonid"],
    }).json()
    assert again["cached"] is True and len(repo.searches) == 1
    fresh = api.post("/api/instances/wlo-prod/aggregate", json={"refresh": True}).json()
    assert fresh["cached"] is False and fresh["facets"] == [] and fresh["filters"] == []


def test_aggregate_never_returns_nodes(api):
    r = api.post("/api/instances/wlo-prod/aggregate", json={"facets": ["ccm:taxonid"]})
    assert "nodes" not in r.json()
    assert "ROHDATEN" not in r.text and "GEHEIM" not in r.text


def test_aggregate_unresolved_value(api, repo):
    r = api.post("/api/instances/wlo-prod/aggregate", json={
        "filters": [{"property": "ccm:taxonid", "values": ["Mathematik", "Griech"]}]})
    assert r.status_code == 422
    body = r.json()
    assert (body["property"], body["text"]) == ("ccm:taxonid", "Griech")
    assert body["suggestions"][0]["caption"] == "Griechisch"
    assert "Griech" in body["detail"]
    assert repo.searches == []
    r = api.post("/api/instances/wlo-prod/aggregate", json={
        "filters": [{"property": "ccm:taxonid", "values": ["Sport"]}]})
    assert r.status_code == 422 and r.json()["ambiguous"] is True


def test_aggregate_duplicate_values_after_resolution_are_merged(api, repo):
    r = api.post("/api/instances/wlo-prod/aggregate", json={
        "filters": [{"property": "ccm:taxonid", "values": ["Mathematik", DISC + "380"]}]})
    assert r.status_code == 200
    assert repo.bodies()[0]["criteria"] == [{"property": "ccm:taxonid", "values": [DISC + "380"]}]
    assert len(r.json()["filters"][0]["values"]) == 1


def test_facets_on_record_fields_are_refused(api, repo):
    """Eine Facette über Titel oder URL wäre eine Datensatzliste."""
    for prop in ("cclom:title", "ccm:wwwurl", "sys:node-uuid"):
        r = api.post("/api/instances/wlo-prod/aggregate",
                     json={"facets": ["ccm:taxonid", prop]})
        assert r.status_code == 422, prop
        assert r.json()["kind"] == "invalid_query"
    assert repo.searches == []
    ok = api.post("/api/instances/wlo-prod/aggregate",
                  json={"facets": ["ccm:commonlicense_key", "ccm:replicationsource"]})
    assert ok.status_code == 200


def test_facet_values_can_be_used_as_literal_filters(api, repo):
    r = api.post("/api/instances/wlo-prod/aggregate", json={"filters": [
        {"property": "ccm:educationalcontext",
         "values": ["Sekundarstufe I", {"value": "political", "literal": True},
                    {"value": "", "literal": True}]},
        {"property": "ccm:commonlicense_key", "values": ["CUSTOM", "CC_BY_SA", ""]}]})
    assert r.status_code == 200, r.text
    assert repo.bodies()[0]["criteria"] == [
        {"property": "ccm:commonlicense_key", "values": ["", "CC_BY_SA", "CUSTOM"]},
        {"property": "ccm:educationalcontext",
         "values": ["", EC + "sekundarstufe_1", "political"]}]
    hows = [v["how"] for v in r.json()["filters"][0]["values"]]
    assert hows == ["label", "literal", "literal"]


def test_response_keys_are_closed(api):
    body = api.post("/api/instances/wlo-prod/aggregate",
                    json={"facets": ["ccm:taxonid"]}).json()
    assert set(body) == {"instance", "total", "facets", "filters", "fetched_at", "cached"}
    assert set(body["facets"][0]) == {"property", "caption", "other_count", "truncated", "values"}
    assert set(body["facets"][0]["values"][0]) == {"value", "label", "count"}


@pytest.mark.parametrize(("payload", "status"), [
    ({"filters": [{"property": "ccm:taxonid", "values": ["Physik"]},
                  {"property": " ccm:taxonid", "values": ["Mathematik"]}]}, 422),
    ({"facets": ["ccm:taxonid "]}, 422),
    ({"facet_limit": 5001}, 422),
    ({"filters": [{"property": "ccm:x", "values": ["a" * 2001]}]}, 422),
    ({"filters": [{"property": "ccm:x", "values": [{"value": "a", "literal": False}]}]}, 422),
    ({"filters": [{"property": "ccm:taxonid", "values": []}]}, 422),
    ({"filters": [{"property": "ccm:taxonid", "values": ["Physik"]},
                  {"property": "ccm:taxonid", "values": ["Mathematik"]}]}, 422),
    ({"filters": [{"property": "", "values": ["x"]}]}, 422),
    ({"facet_limit": 0}, 422),
    ({"facets": ["a"] * 30}, 422),
    ({"filters": "kaputt"}, 422),
    ({"unbekannt": 1}, 422),
])
def test_aggregate_invalid_input(api, repo, payload, status):
    r = api.post("/api/instances/wlo-prod/aggregate", json=payload)
    assert r.status_code == status
    assert repo.searches == []


@pytest.mark.parametrize(("script", "status", "kind", "upstream"), [
    ([error_response(400, HIDDEN)], 502, "rejected", 400),
    ([error_response(400, "Could not find parameter ccm:x in the query ngsearch")], 502,
     "unknown_property", 400),
    ([httpx.ReadTimeout("x")], 504, "timeout", None),
    ([httpx.Response(200, json={"pagination": {"total": 1}, "facets": 5})], 502,
     "bad_response", 200),
    ([httpx.Response(200, text="<html>")], 502, "bad_response", 200),
    ([error_response(500, "all shards failed")], 502, "unavailable", 500),
])
def test_aggregate_upstream_errors(api, repo, script, status, kind, upstream):
    api.get("/api/instances/wlo-prod/fields")  # Vokabular vorab laden
    repo.script = script
    r = api.post("/api/instances/wlo-prod/aggregate",
                 json={"filters": [{"property": "ccm:x", "values": ["y"]}]})
    assert r.status_code == status
    body = r.json()
    assert (body["kind"], body["upstream_status"]) == (kind, upstream)
    assert "Geheim" not in r.text and "stacktrace" not in r.text.lower()


def test_metadataset_failure_is_502(api, repo):
    repo.mds_error = httpx.Response(503, text="x")
    r = api.get("/api/instances/wlo-prod/fields")
    assert r.status_code == 502 and r.json()["kind"] == "unavailable"


def test_instances_are_separate(api, repo):
    api.post("/api/instances/wlo-prod/aggregate", json={})
    api.post("/api/instances/wlo-staging/aggregate", json={})
    hosts = [r.url.host for r in repo.searches]
    assert hosts == ["redaktion.openeduhub.net", "repository.staging.openeduhub.net"]


def test_instance_credentials_only_go_to_their_host(api, repo):
    api.post("/api/instances/eigene/aggregate", json={})
    api.post("/api/instances/wlo-prod/aggregate", json={})
    by_host = {r.url.host: r.headers.get("authorization") for r in repo.searches}
    assert by_host["edu.example.org"].startswith("Basic ")
    assert by_host["redaktion.openeduhub.net"] is None


def test_routes_require_login(settings, repo):
    client = TestClient(create_app(settings, transport=repo.transport()))
    assert client.get("/api/instances/wlo-prod/about").status_code == 401
    assert client.post("/api/instances/wlo-prod/aggregate", json={}).status_code == 401
    assert repo.requests == []
