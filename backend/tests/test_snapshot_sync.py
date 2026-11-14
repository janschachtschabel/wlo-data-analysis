"""Der Abzug selbst: seitenweise über die Zeit, fortsetzbar, ohne Inhalte nach außen."""

import asyncio
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from wda.cache import TTLCache
from wda.edusharing.client import EduSharingClient
from wda.instances import Instance
from wda.main import create_app
from wda.snapshot.store import Snapshot
from wda.snapshot.sync import SyncError, status, sync

from .conftest import ADMIN_PASSWORD
from .esdata import DataRepo, make_docs

SECRET = b"g" * 32
SRC, PUB = "ccm:replicationsource", "ccm:oeh_publisher_combined"


async def no_sleep(_):
    return None


def run_sync(repo, snap, mode="full", page=50, clock=time.time):
    async def go():
        async with httpx.AsyncClient(transport=repo.transport()) as http:
            client = EduSharingClient(Instance(id="t", name="T", base_url="https://r.example"),
                                      http, TTLCache(ttl=0))
            return await sync(client, snap, SECRET, mode, page=page, pause=0, sleep=no_sleep,
                              clock=clock)
    return asyncio.run(go())


@pytest.fixture
def snap(tmp_path):
    snap = Snapshot(tmp_path / "abzug" / "t.sqlite3")
    yield snap
    snap.close()


def test_full_sync_pages_through_everything(snap):
    repo = DataRepo()
    out = run_sync(repo, snap)
    assert out["count"] == snap.count() == len(repo.docs) == 400
    assert repo.node_requests >= 8  # 400 Materialien in Seiten zu 50, je Seite eine Überlappung
    state = status(snap, False)
    assert state["status"] == "ok" and state["complete"] and state["total"] == 400
    # jede Anfrage: aufsteigend sortiert, nur die gebrauchten Felder, Fortsetzung über die Zeit
    pages = [r for r in repo.requests if r.url.params.get("maxItems") == "50"]
    assert all(r.url.params["sortProperties"] == "cm:created" for r in pages)
    assert all("cclom:title" in r.url.params.get_list("propertyFilter") for r in pages)
    assert all(r.url.params["skipCount"] == "0" for r in pages)
    by_source = snap.run(lambda con: dict(con.execute(
        "SELECT name, COUNT(*) FROM memberships WHERE kind='source' GROUP BY name")))
    assert by_source == {s: sum(1 for d in repo.docs if s in d[SRC])
                         for s in ("oersi_spider", "youtube_spider", "bpb_spider")}


def test_many_materials_with_the_same_time_are_paged_by_offset(snap):
    docs = make_docs(260)
    for doc in docs[40:200]:  # Massenimport: 160 Materialien in derselben Millisekunde
        doc["_cm:created"] = [str(DataRepo.T0 + 40 * 3_600_000)]
    repo = DataRepo(docs)
    run_sync(repo, snap, page=50)
    assert snap.count() == 260
    assert any(r.url.params.get("skipCount") not in (None, "0") for r in repo.requests)


def test_a_stopped_sync_resumes_and_a_failed_one_says_why(snap):
    repo = DataRepo()
    calls = {"n": 0}
    real = repo.handler

    def flaky(request):
        if request.url.params.get("maxItems") == "50":
            calls["n"] += 1
            if calls["n"] == 4:
                return httpx.Response(400, json={"error": "x", "message": "kaputt"})
        return real(request)

    repo.handler = flaky
    with pytest.raises(Exception):  # noqa: B017, PT011 - die Art steht im Stand
        run_sync(repo, snap)
    state = status(snap, False)
    assert state["status"] == "failed" and state["error"] and state["resumable"]
    assert not state["complete"] and 0 < snap.count() < 400
    before = repo.node_requests
    run_sync(repo, snap)  # setzt fort, statt von vorn zu beginnen
    assert snap.count() == 400 and repo.node_requests - before < 8
    assert status(snap, False)["status"] == "ok"


def test_second_full_pass_removes_what_is_gone_and_update_fetches_changes(snap):
    docs = make_docs(120)
    repo = DataRepo(docs)
    run_sync(repo, snap, clock=lambda: (DataRepo.T0 + 119 * 7_200_000) / 1000 + 10)
    del docs[:20]
    # ein geändertes Material: neue Änderungszeit, jetzt mit anderer Quelle
    stamp = DataRepo.T0 + 500 * 7_200_000
    for n, doc in enumerate(docs):
        doc["_id"] = [f"node-{n + 20}"]
        doc["_cm:created"] = [str(DataRepo.T0 + (n + 20) * 3_600_000)]
        doc["_cm:modified"] = [str(DataRepo.T0 + (n + 20) * 7_200_000)]
    docs[5]["_cm:modified"] = [str(stamp)]
    docs[5][SRC] = ["neu_spider"]
    before = repo.node_requests
    out = run_sync(repo, snap, mode="update")
    assert repo.node_requests - before == 1 and out["removed"] == 0
    assert snap.count() == 120  # Nachziehen bemerkt Gelöschtes nicht
    assert snap.run(lambda con: con.execute(
        "SELECT name FROM memberships WHERE node='node-25' AND kind='source'").fetchone()[0]
    ) == "neu_spider"
    out = run_sync(repo, snap)
    assert out["removed"] == 20 and snap.count() == 100


def test_update_needs_a_full_snapshot(snap):
    with pytest.raises(SyncError):
        run_sync(DataRepo(), snap, mode="update")


@pytest.fixture
def api(settings):
    repo = DataRepo()
    with TestClient(create_app(settings, transport=httpx.MockTransport(repo.handler))) as client:
        assert client.post("/api/auth/login",
                           json={"password": ADMIN_PASSWORD}).status_code == 204
        client.repo = repo
        yield client


def wait(api, instance="wlo-prod"):
    for _ in range(200):
        state = api.get(f"/api/snapshot/{instance}").json()
        if state["status"] != "running":
            return state
        time.sleep(0.02)
    raise AssertionError("Abzug wird nicht fertig")


def test_routes_start_report_and_answer_queries_without_content(api, settings):
    assert api.get("/api/snapshot/wlo-prod").json()["status"] == "none"
    assert api.get("/api/snapshot/gibtsnicht").status_code == 404
    r = api.post("/api/query", json={"instance": "wlo-prod",
                                     "snapshot": {"analysis": "freitext"}})
    assert r.status_code == 422 and "Abzug" in r.json()["detail"]
    assert api.post("/api/snapshot/wlo-prod/start", json={"mode": "update"}).status_code == 409
    started = api.post("/api/snapshot/wlo-prod/start", json={})
    assert started.status_code == 202 and started.json()["status"] == "running"
    state = wait(api)
    assert state["status"] == "ok" and state["count"] == 400 and state["complete"]
    result = api.post("/api/query", json={
        "instance": "wlo-prod", "snapshot": {"analysis": "freitext", "by": "source"}}).json()
    docs = api.repo.docs
    row = next(r for r in result["rows"] if r["key"] == "bpb_spider")
    assert row["values"][0] == sum(1 for d in docs if "bpb_spider" in d[SRC])
    ids = [c["id"] for c in result["columns"]]
    having = [n for n, d in enumerate(docs) if "bpb_spider" in d[SRC]]
    assert row["values"][ids.index("titel")] == round(
        100 * sum(1 for n in having if n % 5) / len(having), 2)
    # nichts, was eine Route liefert, enthält Freitext oder Adressen der Materialien
    everything = "".join(api.get(path).text for path in (
        "/api/snapshot/wlo-prod", "/api/instances")) + r.text + str(result)
    assert "Material Nummer" not in everything and "example/pfad" not in everything
    files = list((settings.data_dir / "abzug").glob("wlo-prod.sqlite3*"))
    raw = b"".join(p.read_bytes() for p in files)
    assert files and b"Material Nummer" not in raw and b"/pfad/" not in raw
    assert api.delete("/api/snapshot/wlo-prod").json()["count"] == 0


def test_stop_and_restart(api):
    api.post("/api/snapshot/wlo-prod/start", json={})
    assert api.post("/api/snapshot/wlo-prod/stop").json()["status"] in ("stopped", "ok")
    api.post("/api/snapshot/wlo-prod/start", json={})
    assert wait(api)["count"] == 400


def test_dashboard_runs_from_the_snapshot(api):
    from wda.api.builtin import bundles
    from wda.store.specs import parse_spec

    entry = bundles()["tiefenpruefung"]["definitions"][0]
    parsed = parse_spec("dashboard", entry["spec"])
    cells = set()
    for tile in parsed.tiles:
        assert tile.query is not None and tile.query.snapshot is not None, tile.id
        area = {(x, y) for x in range(tile.layout.x, tile.layout.x + tile.layout.w)
                for y in range(tile.layout.y, tile.layout.y + tile.layout.h)}
        assert not area & cells and tile.layout.x + tile.layout.w <= 12, tile.id
        cells |= area
    assert api.post("/api/builtin/tiefenpruefung/install").status_code == 200
    before = api.post("/api/definitions/builtin-tiefenpruefung/run").json()
    assert before["status"] == "failed"  # ohne Abzug keine Zahl
    assert all("Abzug" in r["error"]["detail"] for r in before["results"])
    api.post("/api/snapshot/wlo-prod/start", json={})
    wait(api)
    searches = len(api.repo.requests)
    run = api.post("/api/definitions/builtin-tiefenpruefung/run").json()
    assert run["status"] == "ok", [(r["key"], r["error"]) for r in run["results"] if r["error"]]
    assert len(api.repo.requests) == searches  # nichts davon fragt das Repositorium
    report = api.get(f"/api/runs/{run['id']}/report").json()
    assert len(report["blocks"]) == len(run["results"]) == 18
    for fmt in ("html", "csv", "md", "json"):
        text = api.get(f"/api/runs/{run['id']}/export?format={fmt}").text
        assert "Material Nummer" not in text and "/pfad/" not in text, fmt
