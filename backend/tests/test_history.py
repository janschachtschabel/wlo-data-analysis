"""Zeitreihen aus gespeicherten Läufen: nur Zählungen, schnell auch bei vielen Läufen."""

import time

import pytest
from fastapi.testclient import TestClient

from wda.main import create_app

from .conftest import ADMIN_PASSWORD
from .esdata import DISC, DataRepo
from .test_definition_routes import QUERY, TAX, make, tile

M = DISC + "380"


def local_day(stamp):
    """Der Kalendertag eines Laufs in der Zeitzone der Anwendung."""
    from datetime import datetime
    from zoneinfo import ZoneInfo
    return datetime.fromisoformat(stamp.replace("Z", "+00:00")).astimezone(
        ZoneInfo("Europe/Berlin")).date().isoformat()


@pytest.fixture
def repo():
    return DataRepo()


@pytest.fixture
def api(settings, repo):
    with TestClient(create_app(settings, transport=repo.transport())) as client:
        assert client.post("/api/auth/login",
                           json={"password": ADMIN_PASSWORD}).status_code == 204
        yield client


def run(api, def_id, **body):
    r = api.post(f"/api/definitions/{def_id}/run", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def test_series_over_runs_with_default_and_chosen_cells(api, repo):
    m = make(api, "metric", "Fächer", {"query": QUERY})
    first = run(api, m["id"])
    repo.docs.extend(repo.docs[:10])  # der Bestand wächst
    second = run(api, m["id"], refresh=True)

    keys = api.get(f"/api/definitions/{m['id']}/history/keys").json()
    assert keys["run_id"] == second["id"]
    block = keys["blocks"][0]
    assert (block["key"], block["title"]) == ("main", "Fächer")
    assert block["rows"][-1] == {"key": "total:__total__", "label": "Gesamt", "kind": "total"}
    assert any(r["key"] == "value:" + M and r["label"] == "Mathematik" for r in block["rows"])
    column = block["columns"][0]["id"]

    hist = api.get(f"/api/definitions/{m['id']}/history").json()
    assert [s["row"] for s in hist["series"]] == ["total:__total__"]
    assert [p["run_id"] for p in hist["points"]] == [first["id"], second["id"]]
    assert [p["values"] for p in hist["points"]] == [[400], [410]]
    assert set(hist["points"][0]) == {"run_id", "started_at", "status", "definition_version",
                                      "origin", "changed", "values"}

    chosen = api.get(f"/api/definitions/{m['id']}/history", params={
        "key": "main", "row": ["value:" + M, "total:__total__"], "column": column}).json()
    assert [s["label"] for s in chosen["series"]] == ["Mathematik", "Gesamt"]
    math = [r for r in second["results"][0]["result"]["rows"] if r["key"] == M][0]["values"][0]
    assert chosen["points"][1]["values"] == [math, 410]


def test_selection_is_checked_and_limited(api):
    m = make(api, "metric", "Fächer", {"query": {**QUERY, "columns": {
        "property": "ccm:educationalcontext", "top": 5}}})
    empty = api.get(f"/api/definitions/{m['id']}/history").json()
    assert empty["points"] == [] and empty["series"] == []
    assert empty["notes"][0]["code"] == "no_runs"
    run(api, m["id"])
    keys = api.get(f"/api/definitions/{m['id']}/history/keys").json()["blocks"][0]
    rows = [r["key"] for r in keys["rows"]]
    columns = [c["id"] for c in keys["columns"]]
    for params in ({"key": "gibtsnicht"}, {"row": "value:unbekannt"}, {"column": "x"}):
        r = api.get(f"/api/definitions/{m['id']}/history", params=params)
        assert (r.status_code, r.json()["kind"]) == (422, "invalid_request"), params
    many = api.get(f"/api/definitions/{m['id']}/history",
                   params={"row": rows[:3], "column": columns[:3]})
    assert many.status_code == 422 and "höchstens 8" in many.json()["detail"]
    assert api.get("/api/definitions/gibtsnicht1/history").status_code == 404
    assert api.get(f"/api/definitions/{m['id']}/history",
                   params={"from": "gestern"}).status_code == 422


def test_other_instance_is_a_series_of_its_own_and_versions_are_marked(api):
    m = make(api, "metric", "Fächer", {"query": QUERY})
    run(api, m["id"])
    run(api, m["id"], instance="wlo-staging")
    api.put(f"/api/definitions/{m['id']}", json={"version": 1, "spec": {"query": {
        **QUERY, "filters": [{"property": TAX, "values": [M]}]}}})
    run(api, m["id"])
    own = api.get(f"/api/definitions/{m['id']}/history").json()
    assert [p["definition_version"] for p in own["points"]] == [1, 2]
    assert [n["code"] for n in own["notes"]] == ["definition_changed"]
    staging = api.get(f"/api/definitions/{m['id']}/history",
                      params={"instance": "wlo-staging"}).json()
    assert len(staging["points"]) == 1


def test_range_limit_and_deleted_or_failed_runs(api):
    d = make(api, "dashboard", "D", {"tiles": [
        tile("ok", query=QUERY),
        tile("kaputt", query={"instance": "wlo-prod", "filters": [
            {"property": TAX, "values": ["Mathematk"]}]})]})
    runs = [run(api, d["id"]) for _ in range(4)]
    hist = api.get(f"/api/definitions/{d['id']}/history", params={"limit": 2}).json()
    assert [p["run_id"] for p in hist["points"]] == [r["id"] for r in runs[2:]]
    assert [b["key"] for b in api.get(
        f"/api/definitions/{d['id']}/history/keys").json()["blocks"]] == ["ok"]
    api.delete(f"/api/runs/{runs[3]['id']}")
    hist = api.get(f"/api/definitions/{d['id']}/history").json()
    assert len(hist["points"]) == 3
    day = local_day(runs[0]["started_at"])
    assert len(api.get(f"/api/definitions/{d['id']}/history",
                       params={"from": day, "to": day}).json()["points"]) == 3
    assert api.get(f"/api/definitions/{d['id']}/history",
                   params={"to": "2000-01-01"}).json()["points"] == []


def test_five_hundred_runs_are_read_fast(api):
    m = make(api, "metric", "Fächer", {"query": QUERY})
    real = run(api, m["id"])
    db = api.app.state.services.db

    def clone(con):
        points = [tuple(r) for r in con.execute(
            "SELECT key, row_key, column_id, value FROM points WHERE run_id=?", (real["id"],))]
        for n in range(500):
            rid = f"klon-{n:04d}"
            con.execute(
                "INSERT INTO runs (id, definition_id, kind, name, definition_version, spec,"
                " status, started_at, finished_at) SELECT ?, definition_id, kind, name, 1, spec,"
                " status, ?, finished_at FROM runs WHERE id=?",
                (rid, f"2027-01-01T00:{n // 60:02d}:{n % 60:02d}+00:00", real["id"]))
            con.executemany("INSERT INTO points VALUES (?,?,?,?,?)",
                            [(rid, k, r, c, (v or 0) + n) for k, r, c, v in points])

    db.write_now(clone)
    started = time.perf_counter()
    hist = api.get(f"/api/definitions/{m['id']}/history", params={"limit": 1000}).json()
    elapsed = time.perf_counter() - started
    assert len(hist["points"]) == 501
    assert hist["points"][-1]["values"] == [400 + 499]
    assert elapsed < 0.5, f"{elapsed:.3f} s"


def test_columns_of_blocks_side_by_side_carry_the_block_name(api):
    bundle = api.get("/api/builtin").json()[0]
    assert api.post(f"/api/builtin/{bundle['id']}/install").status_code in (200, 201)
    template = next(d for d in api.get("/api/definitions").json() if d["kind"] == "template")
    run(api, template["id"])
    keys = api.get(f"/api/definitions/{template['id']}/history/keys").json()
    matrix = next(b for b in keys["blocks"] if len(b["columns"]) > 8)
    labels = [c["label"] for c in matrix["columns"]]
    assert len(set(labels)) == len(labels)
    assert all(" · " in label for label in labels)
    hist = api.get(f"/api/definitions/{template['id']}/history?key={matrix['key']}").json()
    assert len({s["label"] for s in hist["series"]}) == len(hist["series"]) == 8
