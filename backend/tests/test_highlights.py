"""„Was ist auffällig?“: die größten Veränderungen zwischen den beiden jüngsten gespeicherten
Läufen jeder Auswertung. Gelesen werden nur die Zählungen der Läufe."""

from .esdata import DISC
from .test_definition_routes import QUERY, make
from .test_history import api, repo, run  # noqa: F401

M = DISC + "380"


def test_highlights_name_the_biggest_changes(api, repo):  # noqa: F811
    assert api.get("/api/highlights").json() == {"items": [], "watched": 0, "compared": 0}
    m = make(api, "metric", "Fächer", {"query": QUERY})
    other = make(api, "metric", "Ruhig", {"query": QUERY})
    run(api, m["id"])
    run(api, other["id"])
    one = api.get("/api/highlights").json()
    assert one["items"] == [] and one["watched"] == 2 and one["compared"] == 0
    math = [d for d in repo.docs if M in d["ccm:taxonid"]]
    repo.docs.extend([dict(d) for d in math[:40]])  # Mathematik wächst kräftig
    second = run(api, m["id"], refresh=True)
    out = api.get("/api/highlights").json()
    assert out["compared"] == 1
    top = out["items"][0]
    assert top["definition_id"] == m["id"] and top["run_id"] == second["id"]
    assert top["name"] == "Fächer" and top["row"] == "Mathematik"
    assert top["after"] - top["before"] == top["delta"] == 40
    assert top["percent"] == round(100 * 40 / top["before"], 1)
    assert {"previous_run_id", "started_at", "previous_started_at", "block", "column"} <= set(top)
    # je Auswertung höchstens drei Zeilen, die größte Veränderung zuerst; nichts Unverändertes
    assert len(out["items"]) <= 3 and all(i["delta"] for i in out["items"])
    assert all(i["definition_id"] == m["id"] for i in out["items"])
    scores = [abs(i["percent"]) for i in out["items"]]
    assert scores == sorted(scores, reverse=True)


def test_highlights_skip_small_noise_and_changed_definitions(api, repo):  # noqa: F811
    m = make(api, "metric", "Fächer", {"query": QUERY})
    run(api, m["id"])
    repo.docs.extend([dict(d) for d in repo.docs[:2]])  # zwei Materialien mehr: Rauschen
    run(api, m["id"], refresh=True)
    assert api.get("/api/highlights").json()["items"] == []
    # nach einer Änderung der Abfrage sind die Läufe nicht vergleichbar
    changed = {**QUERY, "filters": [{"property": "ccm:taxonid", "values": ["Mathematik"]}]}
    put = api.put(f"/api/definitions/{m['id']}", json={
        "name": "Fächer", "spec": {"query": changed}, "version": m["version"]})
    assert put.status_code == 200, put.text
    run(api, m["id"], refresh=True)
    out = api.get("/api/highlights").json()
    assert out["items"] == [] and out["compared"] == 0
    assert api.get("/api/highlights", params={"limit": 0}).status_code == 422


def test_trends_give_each_tile_its_total_over_the_saved_runs(api, repo):  # noqa: F811
    m = make(api, "metric", "Fächer", {"query": QUERY})
    assert api.get(f"/api/definitions/{m['id']}/trends").json() == {"runs": [], "blocks": {}}
    first = run(api, m["id"])
    assert api.get(f"/api/definitions/{m['id']}/trends").json()["runs"] == []
    repo.docs.extend([dict(d) for d in repo.docs[:10]])
    second = run(api, m["id"], refresh=True)
    out = api.get(f"/api/definitions/{m['id']}/trends").json()
    assert [r["run_id"] for r in out["runs"]] == [first["id"], second["id"]]
    assert out["blocks"] == {"main": [400, 410]}
    # nach einer Änderung der Abfrage beginnt die Linie neu
    changed = {**QUERY, "filters": [{"property": "ccm:taxonid", "values": ["Mathematik"]}]}
    assert api.put(f"/api/definitions/{m['id']}", json={
        "name": "Fächer", "spec": {"query": changed}, "version": m["version"]}).status_code == 200
    run(api, m["id"], refresh=True)
    assert api.get(f"/api/definitions/{m['id']}/trends").json()["runs"] == []
    assert api.get("/api/definitions/gibt-es-nicht/trends").status_code == 404
