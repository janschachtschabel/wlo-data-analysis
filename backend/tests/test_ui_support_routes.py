"""Was die Oberfläche zusätzlich braucht: Läufe filtern und aufräumen, letzter Lauf an der
Definition, Abweichung von mitgelieferten Vorlagen."""

import sqlite3

from .test_definition_routes import QUERY, TAX, api, make, repo, tile  # noqa: F401


def test_runs_filter_by_status_and_kind_and_bulk_delete(api):  # noqa: F811
    m = make(api, "metric", "Fächer", {"query": QUERY})
    d = make(api, "dashboard", "Überblick", {"tiles": [
        tile("fach", metric_id=m["id"]),
        tile("kaputt", query={"instance": "wlo-prod", "filters": [
            {"property": TAX, "values": ["Mathematk"]}]})]})
    ok1 = api.post(f"/api/definitions/{m['id']}/run").json()
    ok2 = api.post(f"/api/definitions/{m['id']}/run").json()
    partial = api.post(f"/api/definitions/{d['id']}/run").json()

    ids = lambda **params: [r["id"] for r in api.get("/api/runs", params=params).json()]  # noqa: E731
    assert ids(status="ok") == [ok2["id"], ok1["id"]]
    assert ids(status="partial") == [partial["id"]]
    assert ids(kind="dashboard") == [partial["id"]]
    assert ids(kind="metric", status="partial") == []
    assert api.get("/api/runs", params={"status": "kaputt"}).status_code == 422
    assert api.get("/api/runs", params={"kind": "filter; DROP"}).status_code == 422

    # ohne Angabe wird nichts gelöscht
    assert api.delete("/api/runs").status_code == 422
    gone = api.delete("/api/runs", params={"definition": m["id"]})
    assert gone.status_code == 200 and gone.json() == {"deleted": 2}
    assert ids() == [partial["id"]]
    assert api.delete("/api/runs", params={"definition": m["id"]}).json() == {"deleted": 0}
    # Läufe gelöschter Definitionen
    api.delete(f"/api/definitions/{d['id']}")
    assert api.delete("/api/runs", params={"definition": "none"}).json() == {"deleted": 1}
    assert ids() == []


def test_definition_list_names_the_last_run(api):  # noqa: F811
    m = make(api, "metric", "Fächer", {"query": QUERY})
    other = make(api, "metric", "Andere", {"query": QUERY})
    api.post(f"/api/definitions/{m['id']}/run")
    last = api.post(f"/api/definitions/{m['id']}/run").json()
    listed = {d["id"]: d for d in api.get("/api/definitions").json()}
    assert listed[m["id"]]["last_run"] == {
        "id": last["id"], "status": "ok", "started_at": last["started_at"]}
    assert listed[other["id"]]["last_run"] is None


def test_stored_result_does_not_repeat_the_query(api, settings):  # noqa: F811
    m = make(api, "metric", "Fächer", {"query": QUERY})
    run = api.post(f"/api/definitions/{m['id']}/run").json()
    assert run["results"][0]["result"]["provenance"]["spec"] == run["results"][0]["query"]
    con = sqlite3.connect(settings.data_dir / "store.sqlite3")
    stored = con.execute("SELECT result FROM run_results").fetchone()[0]
    con.close()
    assert '"spec"' not in stored
    # beim Lesen ist alles wieder da
    assert api.get(f"/api/runs/{run['id']}").json() == run


def test_builtin_reports_changes_and_can_be_reset(api):  # noqa: F811
    api.post("/api/builtin/oer-statistik/install")
    def entry():
        return next(b for b in api.get("/api/builtin").json()
                    if b["id"] == "oer-statistik")["definitions"][0]

    assert (entry()["installed"], entry()["changed"]) == (True, False)

    current = api.get("/api/definitions/builtin-oer-statistik").json()
    spec = current["spec"]
    spec["title"] = "Meine Statistik"
    api.put("/api/definitions/builtin-oer-statistik", json={"version": 1, "spec": spec})
    assert entry()["changed"] is True
    # ohne mode bleibt die eigene Fassung
    api.post("/api/builtin/oer-statistik/install")
    assert entry()["changed"] is True

    reset = api.post("/api/builtin/oer-statistik/install", params={"mode": "replace"})
    assert reset.status_code == 200 and reset.json()["definitions"][0]["action"] == "replaced"
    assert entry()["changed"] is False
    assert api.get("/api/definitions/builtin-oer-statistik").json()["spec"]["title"] != (
        "Meine Statistik")
    assert api.post("/api/builtin/oer-statistik/install",
                    params={"mode": "copy"}).status_code == 422
