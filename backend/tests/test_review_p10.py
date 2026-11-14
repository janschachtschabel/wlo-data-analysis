"""Tests aus dem Review von Paket 10 (lokaler Abzug), an die Behebung angepasst."""

import asyncio
import json
import stat
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from tests.conftest import ADMIN_PASSWORD
from tests.esdata import DataRepo, make_docs
from wda.cache import TTLCache
from wda.config import Settings
from wda.edusharing.client import EduSharingClient
from wda.engine.spec import QuerySpec
from wda.instances import Instance
from wda.main import create_app
from wda.snapshot.analyses import run_analysis
from wda.snapshot.features import features
from wda.snapshot.store import Snapshot
from wda.snapshot.sync import SyncError, status, sync

SECRET = b"g" * 32
SRC = "ccm:replicationsource"
INST = Instance(id="t", name="T", base_url="https://r.example")


async def no_sleep(_):
    return None


def run_sync(repo, snap, mode="full", page=50, clock=time.time):
    async def go():
        async with httpx.AsyncClient(transport=repo.transport()) as http:
            client = EduSharingClient(
                Instance(id="t", name="T", base_url="https://r.example"), http, TTLCache(ttl=0)
            )
            return await sync(
                client, snap, SECRET, mode, page=page, pause=0, sleep=no_sleep, clock=clock
            )

    return asyncio.run(go())


@pytest.fixture
def snap(tmp_path):
    s = Snapshot(tmp_path / "abzug" / "t.sqlite3")
    yield s
    s.close()


def make_api(tmp_path, repo, page=10):
    settings = Settings(
        admin_password=ADMIN_PASSWORD,
        cookie_secure=False,
        data_dir=tmp_path,
        snapshot_page=page,
        instances="[]",
    )
    c = TestClient(create_app(settings, transport=httpx.MockTransport(lambda r: repo.handler(r))))
    c.__enter__()
    assert c.post("/api/auth/login", json={"password": ADMIN_PASSWORD}).status_code == 204
    return c, settings


def wait(api, instance="wlo-prod"):
    for _ in range(500):
        s = api.get(f"/api/snapshot/{instance}").json()
        if s["status"] != "running":
            return s
        time.sleep(0.02)
    raise AssertionError("nicht fertig")


# ---- W1: "Fortsetzen" nach abgebrochenem Nachziehen wird zum Schein-Vollabzug
def test_full_after_failed_update_is_fake_resume(tmp_path):
    docs = make_docs(60)
    repo = DataRepo(docs)
    api, _ = make_api(tmp_path, repo)
    try:
        api.post("/api/snapshot/wlo-prod/start", json={})
        assert wait(api)["status"] == "ok"
        now = int(time.time() * 1000)
        for i, d in enumerate(docs[:40]):
            d["_cm:modified"] = [str(now + 1000 + i)]
        docs[39][SRC] = ["neu_spider"]  # geändert, aber vom abgebrochenen Nachziehen nicht erreicht
        real, calls = repo.handler, {"n": 0}

        def flaky(request):
            if request.url.params.get("sortProperties") == "cm:modified":
                calls["n"] += 1
                if calls["n"] == 3:
                    return httpx.Response(400, json={"error": "x", "message": "kaputt"})
            return real(request)

        repo.handler = flaky
        api.post("/api/snapshot/wlo-prod/start", json={"mode": "update"})
        s = wait(api)
        # ein abgebrochenes Nachziehen lässt sich nicht als voller Lauf „fortsetzen“
        assert s["status"] == "failed" and s["mode"] == "update" and not s["resumable"], s
        assert s["complete"]  # der Stand des letzten ganzen Abzugs gilt weiter
        repo.handler = real
        # Material verschwindet im Repositorium; ein Vollabzug müsste es entfernen
        docs.pop(59)
        before = repo.node_requests
        api.post("/api/snapshot/wlo-prod/start", json={"mode": "full"})  # Knopf "Fortsetzen"
        s = wait(api)
        used = repo.node_requests - before
        assert s["status"] == "ok" and s["mode"] == "full"
        snap = api.app.state.services.snapshot("wlo-prod")
        src = snap.run(
            lambda con: [
                r[0]
                for r in con.execute(
                    "SELECT name FROM memberships WHERE node='node-39' AND kind='source'"
                )
            ]
        )
        # Erwartung an einen echten Vollabzug:
        api.post("/api/snapshot/wlo-prod/start", json={"mode": "update"})
        wait(api)
        src2 = snap.run(
            lambda con: [
                r[0]
                for r in con.execute(
                    "SELECT name FROM memberships WHERE node='node-39' AND kind='source'"
                )
            ]
        )
        assert (used >= 6, s["count"], src, src2) == (True, 59, ["neu_spider"], ["neu_spider"]), (
            used,
            s["count"],
            src,
            src2,
        )
    finally:
        api.__exit__(None, None, None)


# ---- ein Knoten mit einzelnem Surrogat blockiert den Abzug dauerhaft
def test_lone_surrogate_is_stored(snap):
    f = features({"ref": {"id": "a"}, "properties": {
        "cclom:title": ["kaputt \ud800 titel"], SRC: ["quelle \ud800"],
        "ccm:wwwurl": ["https://a.example/\ud800"]}}, SECRET)
    assert f.title_key and f.url_key
    snap.put([f], 1)
    assert snap.count() == 1


def test_a_broken_node_is_skipped_and_counted(snap, monkeypatch):
    real = features

    def picky(node, secret):
        if node["ref"]["id"] == "node-12":
            raise ValueError("Inhalt, der nicht ins Protokoll gehört")
        return real(node, secret)

    monkeypatch.setattr("wda.snapshot.sync.features", picky)
    run_sync(DataRepo(make_docs(30)), snap, page=10)
    s = status(snap, False)
    assert s["status"] == "ok" and s["count"] == 29 and s["skipped"] >= 1, s


def test_one_outage_does_not_end_the_sync(snap):
    repo = DataRepo(make_docs(60))
    real, calls = repo.handler, {"n": 0}

    def flaky(request):
        if request.url.params.get("maxItems") == "10":
            calls["n"] += 1
            if calls["n"] == 3:
                return httpx.Response(503, text="gerade nicht")
        return real(request)

    repo.handler = flaky
    run_sync(repo, snap, page=10)
    assert snap.count() == 60 and status(snap, False)["status"] == "ok"


def test_naive_or_extreme_iso(snap):
    for iso in (
        "0001-01-01T00:00:00",
        "9999-12-31T23:59:59",
        "0001-01-01T00:00:00Z",
        "2024-05-01T10:00:00+0200",
    ):
        assert features({"ref": {"id": "a"}, "createdAt": iso, "properties": {}}, SECRET)


# ---- kurze Seite mitten im Lauf: Abzug hält sich für fertig und löscht den Rest
def test_short_page_ends_pass_and_deletes(snap):
    repo = DataRepo(make_docs(200))
    run_sync(repo, snap, page=50)
    assert snap.count() == 200
    real, calls = repo.handler, {"n": 0}

    def short(request):
        resp = real(request)
        if request.url.params.get("maxItems") == "50":
            calls["n"] += 1
            if calls["n"] == 2:
                data = json.loads(resp.content)
                data["nodes"] = data["nodes"][:49]
                return httpx.Response(200, json=data)
        return resp

    repo.handler = short
    with pytest.raises(SyncError):
        run_sync(repo, snap, page=50)
    s = status(snap, False)
    assert snap.count() == 200 and s["status"] == "failed" and s["resumable"], s
    run_sync(repo, snap, page=50)  # fortgesetzt geht es zu Ende
    assert snap.count() == 200 and status(snap, False)["status"] == "ok"


# ---- Speichern einer Instanz (nur Name geändert) verwirft den Abzug; forget bei laufendem Abzug
def test_forget_semantics(tmp_path):
    repo = DataRepo(make_docs(60))
    api, settings = make_api(tmp_path, repo)
    try:
        body = {"id": "x1", "name": "X", "base_url": "https://r.example"}
        api.post("/api/instances", json=body)
        api.post("/api/snapshot/x1/start", json={})
        assert wait(api, "x1")["count"] == 60
        api.put(
            "/api/instances/x1", json={"name": "Nur umbenannt", "base_url": "https://r.example"}
        )
        s = api.get("/api/snapshot/x1").json()
        assert s["count"] == 60, "Abzug nach bloßer Umbenennung weg"
    finally:
        api.__exit__(None, None, None)


def test_forget_while_running(tmp_path):
    repo = DataRepo(make_docs(80))
    real = repo.handler

    def slow(request):
        if request.url.params.get("maxItems"):
            time.sleep(0.02)
        return real(request)

    repo.handler = slow
    api, settings = make_api(tmp_path, repo)
    try:
        body = {"id": "x1", "name": "X", "base_url": "https://r.example"}
        assert api.post("/api/instances", json=body).status_code == 201
        api.post("/api/snapshot/x1/start", json={})
        time.sleep(0.1)
        assert api.get("/api/snapshot/x1").json()["status"] == "running"
        assert api.delete("/api/instances/x1").status_code == 204
        time.sleep(0.5)
        left = sorted(p.name for p in (settings.data_dir / "abzug").iterdir())
        assert not [n for n in left if n.startswith("x1.")]
        # neu anlegen und sofort starten
        assert api.post("/api/instances", json=body).status_code == 201
        api.post("/api/snapshot/x1/start", json={})
        s = wait(api, "x1")
        assert s["status"] == "ok" and s["count"] == 80
    finally:
        api.__exit__(None, None, None)


def test_perms(tmp_path):
    repo = DataRepo(make_docs(30))
    api, settings = make_api(tmp_path, repo)
    try:
        api.post("/api/snapshot/wlo-prod/start", json={})
        wait(api)
        d = settings.data_dir / "abzug"
        for p in d.iterdir():
            assert stat.S_IMODE(p.stat().st_mode) == 0o600
        assert stat.S_IMODE(d.stat().st_mode) == 0o700
    finally:
        api.__exit__(None, None, None)


# ---- Status "running" bleibt nach Absturz im Zustand: Hinweis in Auswertung
def test_running_note_after_crash(snap):
    repo = DataRepo(make_docs(30))
    run_sync(repo, snap, page=10)
    snap.set_state(status="running")  # so steht es nach einem Absturz mitten im zweiten Lauf
    out = run_analysis(snap, QuerySpec(instance="t", snapshot={"analysis": "freitext"}), INST)
    codes = [n["code"] for n in out["provenance"]["notes"]]
    assert "snapshot_running" not in codes


def node(n, created="2024-05-01T10:00:00Z", **props):
    base = {"cclom:title": [f"Titel {n}"]}
    base.update(props)
    return {
        "ref": {"id": f"id-{n}"},
        "createdAt": created,
        "modifiedAt": "2026-01-01T00:00:00Z",
        "properties": base,
    }


def fill(snap, nodes, now=1_800_000_000):
    snap.put([features(n, SECRET) for n in nodes], 1)
    snap.set_state(status="ok", finished_at=now)


def ask(snap, **q):
    return run_analysis(snap, QuerySpec(instance="t", snapshot=q), INST)


def test_nodes_without_group_vanish_silently(snap):
    S = "ccm:replicationsource"
    fill(snap, [node(1, **{S: ["a"]}), node(2, **{S: ["a"]}), node(3), node(4), node(5)])
    out = ask(snap, analysis="freitext", by="source", show="count")
    rows = {r["key"]: r["values"] for r in out["rows"]}
    [n["code"] for n in out["provenance"]["notes"]]
    assert sum(v[0] for v in rows.values()) == out["columns"][0]["total"], (
        "Zeilen ergeben 2, Gesamt 5; keine Zeile/kein Hinweis für 3 Materialien ohne Quelle"
    )


def test_hosts_top_cut_without_note(snap):
    fill(snap, [node(i, **{"ccm:wwwurl": [f"https://h{i}.example/x"]}) for i in range(30)])
    out = ask(snap, analysis="rechner", top=5)
    notes = [n["code"] for n in out["provenance"]["notes"]]
    assert "rows_top" in notes
    # Rechner mit einem einzigen Material stehen nie einzeln
    assert [r["kind"] for r in out["rows"]] == ["other"] and out["rows"][0]["values"][0] == 30


def test_year_edge(snap):
    nodes = [
        node(1, created="2024-12-31T23:30:00Z"),
        node(2, created=None, **{"cm:created": ["9999999999999999"]}),
        node(3, created="0001-01-01T00:00:00Z"),
    ]
    fill(snap, nodes)
    out = ask(snap, analysis="jahrgang")
    assert {r["key"]: r["values"][0] for r in out["rows"]} == {"2024": 1, "__missing__": 2}


def test_url_normalisation():
    def key(u):
        return features(node(1, **{"ccm:wwwurl": [u]}), SECRET).url_key

    f = features(
        node(1, **{"ccm:wwwurl": ["https://nutzer:geheim@intern.schule.example:8080/x?token=abc"]}),
        SECRET,
    )
    assert f.url_host == "intern.schule.example"
    assert key("https://app.example/#/kurs/1") != key("https://app.example/#/kurs/2")


def test_single_material_group_and_host_visible(snap):
    P = "ccm:oeh_publisher_combined"
    fill(
        snap,
        [
            node(
                1,
                **{
                    P: ["Einzelanbieter"],
                    "ccm:wwwurl": ["https://privat-einzel.example/geheimer/pfad"],
                    "cclom:general_description": ["kurz"],
                },
            ),
            *[node(i, **{P: ["Gross"]}) for i in range(2, 30)],
        ],
    )
    out = ask(snap, analysis="beschreibung", by="publisher", show="count")
    hosts = ask(snap, analysis="rechner", top=200)
    assert "geheimer" not in str(out) + str(hosts)
    assert "privat-einzel.example" not in str(hosts)


def test_port_and_hash_path_belong_to_the_address():
    def key(u):
        return features(node(1, **{"ccm:wwwurl": [u]}), SECRET).url_key

    assert key("https://a.example:8443/x") != key("https://a.example/x")
    assert key("https://a.example:443/x") == key("http://www.a.example/x/")
    assert key("https://a.example/x#abschnitt") == key("https://a.example/x")


def test_analysis_does_not_wait_for_a_writer(snap):
    """Auswertungen lesen über eine eigene Verbindung; ein Schreiber hält sie nicht auf."""
    fill(snap, [node(i) for i in range(20)])
    import threading

    release = threading.Event()

    def hold(con):
        release.wait(5)

    t = threading.Thread(target=lambda: snap.run(hold, write=True))
    t.start()
    try:
        time.sleep(0.05)
        began = time.monotonic()
        out = ask(snap, analysis="freitext")
        assert out["base_total"] == 20 and time.monotonic() - began < 2
    finally:
        release.set()
        t.join()
