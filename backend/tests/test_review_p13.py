"""Befunde aus dem Review von Paket 13: Auswertungen, die selbst nach dem Feld des
Kontexts aufschlüsseln."""
import json

import pytest
from fastapi.testclient import TestClient

from tests.esdata import SOURCES, DataRepo
from wda.config import Settings
from wda.main import create_app

PASSWORD = "review-13-passwort"
SRC = "ccm:replicationsource"
PUB = "ccm:oeh_publisher_combined"
TAX = "ccm:taxonid"


def lit(*values):
    return [{"value": v, "literal": True} for v in values]


S0 = [{"property": SRC, "values": lit(SOURCES[0])}]
S01 = [{"property": SRC, "values": lit(SOURCES[0], SOURCES[1])}]


@pytest.fixture
def repo():
    return DataRepo()


@pytest.fixture
def api(tmp_path, repo):
    settings = Settings(admin_password=PASSWORD, cookie_secure=False, data_dir=tmp_path,
                        instances=json.dumps([]))
    with TestClient(create_app(settings, transport=repo.transport())) as client:
        assert client.post("/api/auth/login", json={"password": PASSWORD}).status_code == 204
        yield client


def run(api, query, scope):
    made = api.post("/api/definitions", json={
        "kind": "metric", "name": "M", "spec": {"query": {"instance": "wlo-prod", **query}}})
    assert made.status_code == 201, made.text
    out = api.post(f"/api/definitions/{made.json()['id']}/run",
                   json={"save": False, "scope": scope})
    assert out.status_code == 200, out.text
    return out.json()["results"][0]


def test_a_profile_by_the_scope_field_totals_the_scope_not_the_stock(api, repo):
    out = run(api, {"rows": {"property": SRC, "top": 10},
                    "profile": {"property": "ccm:price", "show": "count"}}, S0)
    inside = repo.brute(lambda d: SOURCES[0] in d[SRC])
    assert [r["values"][0] for r in out["result"]["rows"]] == [inside]
    assert out["result"]["columns"][0]["total"] == inside


def test_a_profile_by_the_scope_field_with_two_values(api, repo):
    out = run(api, {"rows": {"property": SRC, "top": 10},
                    "profile": {"property": "ccm:price", "show": "count"}}, S01)
    both = repo.brute(lambda d: SOURCES[0] in d[SRC] or SOURCES[1] in d[SRC])
    assert len(out["result"]["rows"]) == 2
    assert out["result"]["columns"][0]["total"] == both


def test_a_tiles_own_choice_of_rows_is_not_replaced_by_the_scope(api):
    out = run(api, {"rows": {"property": SRC, "include": lit(SOURCES[1])},
                    "profile": {"property": "ccm:price", "show": "count"}}, S0)
    assert out["error"] is None
    assert out["result"]["rows"] == []


def test_the_builtin_tiles_by_publisher_total_the_publisher(api, repo):
    assert api.post("/api/builtin/quellen/install").status_code == 200
    scope = [{"property": PUB, "values": lit("bpb")}]
    out = api.post("/api/definitions/builtin-quellen/run",
                   json={"save": False, "scope": scope}).json()
    inside = repo.brute(lambda d: "bpb" in d[PUB])
    seen = [r["result"]["columns"][0]["total"] for r in out["results"]
            if r["result"] and (r.get("query") or {}).get("profile")
            and r["query"]["rows"]["property"] == PUB]
    assert seen and set(seen) == {inside}


@pytest.mark.parametrize("query", [
    {"rows": {"property": TAX}, "columns": {"property": SRC}, "percent": "row"},
    {"rows": {"property": TAX}, "columns": {"property": SRC},
     "gaps": {"min_expected": 1, "below": 100}},
    {"rows": {"property": SRC, "groups": [{"label": "A", "values": lit(SOURCES[0])}]}},
])
def test_what_says_nothing_under_one_value_is_dropped(api, query):
    out = run(api, query, S0)
    assert out["error"]["kind"] == "not_applicable", out["error"]


def test_other_stays_when_the_scope_has_several_values(api, repo):
    out = run(api, {"rows": {"property": SRC, "top": 1, "other": True}}, S01)
    if out["error"] is None:
        both = repo.brute(lambda d: SOURCES[0] in d[SRC] or SOURCES[1] in d[SRC])
        assert sum(r["values"][0] for r in out["result"]["rows"]) == both
    else:
        assert "Übrige" in out["error"]["detail"]
