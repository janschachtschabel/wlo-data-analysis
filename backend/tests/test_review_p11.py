"""Tests aus dem zweiten Review (Pakete 10 und 11), an die Behebung angepasst."""

import asyncio
import threading
import time
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient

from tests.conftest import ADMIN_PASSWORD
from tests.esdata import DataRepo, make_docs
from wda.cache import TTLCache
from wda.config import Settings
from wda.edusharing.client import EduSharingClient
from wda.instances import Instance, InstanceRegistry
from wda.main import create_app
from wda.services import Services
from wda.snapshot.store import Snapshot
from wda.snapshot.sync import SyncError, status, sync

SECRET = b"g" * 32


SECRET = b"g" * 32


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


def fail_after(repo, n, code=400):
    real, calls = repo.handler, {"n": 0}

    def h(request):
        if request.url.params.get("maxItems"):
            calls["n"] += 1
            if calls["n"] > n:
                return httpx.Response(code, json={"error": "x", "message": "kaputt"})
        return real(request)

    repo.handler = h
    return real


# --- A2: 98-%-Prüfung nach Fortsetzen mit geschrumpftem Bestand: Dauerschleife


def test_resume_after_shrink_fails_forever(snap):
    docs = make_docs(200)
    repo = DataRepo(docs)
    real = fail_after(repo, 2)
    with pytest.raises(Exception):  # noqa: B017, PT011
        run_sync(repo, snap, page=20)
    s = status(snap, False)
    assert s["status"] == "failed" and s["resumable"] and s["total"] == 200
    repo.handler = real
    del docs[150:165]  # 7,5 % verschwinden vor dem Fortsetzen (noch nicht gelesen)
    outcomes = []
    for _ in range(3):
        try:
            run_sync(repo, snap, page=20)
            outcomes.append("ok")
        except SyncError as e:
            outcomes.append(str(e)[:60])
        s = status(snap, False)
        outcomes.append((s["status"], s["resumable"], s["complete"], s["count"]))
    assert outcomes[0] == "ok", outcomes


NIGHT = datetime(2026, 11, 15, 2, 45, tzinfo=UTC)


def mk(tmp_path, repo, **kw):
    settings = Settings(
        admin_password=ADMIN_PASSWORD,
        data_dir=tmp_path,
        snapshot_page=20,
        snapshot_nightly=kw.pop("nightly", "03:30"),
        timezone="Europe/Berlin",
        **kw,
    )
    return Services(
        settings, InstanceRegistry(None), transport=httpx.MockTransport(lambda r: repo.handler(r))
    )


# C: angehaltener voller Lauf (fortsetzbar) wird vom nächtlichen Nachziehen entwertet
def test_nightly_destroys_resumable_full(tmp_path):
    repo = DataRepo(make_docs(200))

    async def go():
        sv = mk(tmp_path, repo)
        try:
            sv.start_sync("wlo-prod", "full")
            await sv.syncs["wlo-prod"]
            snap = sv.snapshot("wlo-prod")
            snap.set_state(finished_at=(NIGHT - timedelta(hours=12)).timestamp())
            # „Vollständig erneuern“ scheitert mittendrin
            real, calls = repo.handler, {"n": 0}

            def h(request):
                if request.url.params.get("maxItems"):
                    calls["n"] += 1
                    if calls["n"] > 6:
                        return httpx.Response(400, json={"error": "x", "message": "kaputt"})
                return real(request)

            repo.handler = h
            sv.start_sync("wlo-prod", "full")
            await sv.syncs["wlo-prod"]
            s1 = status(snap, False)
            repo.handler = real
            started = await sv.nightly_snapshots(NIGHT)
            if started:
                await sv.syncs["wlo-prod"]
            s2 = status(snap, False)
            return s1, started, s2
        finally:
            await sv.aclose()

    s1, started, s2 = asyncio.run(go())
    assert s1["resumable"] and s1["done"] == 120
    assert s2["resumable"], "Nachts ging die Möglichkeit zum Fortsetzen verloren"


# C: Wettlauf nächtlich gegen Start von Hand
def test_nightly_races_manual_start(tmp_path):
    repo = DataRepo(make_docs(60))

    async def go():
        sv = mk(tmp_path, repo)
        try:
            sv.start_sync("wlo-prod", "full")
            await sv.syncs["wlo-prod"]
            snap = sv.snapshot("wlo-prod")
            snap.set_state(finished_at=(NIGHT - timedelta(hours=12)).timestamp())
            real_set = snap.set_state
            entered, release = threading.Event(), threading.Event()

            def slow_set(**values):
                if "nightly_at" in values and not entered.is_set():
                    entered.set()
                    release.wait(5)
                return real_set(**values)

            snap.set_state = slow_set
            night = asyncio.create_task(sv.nightly_snapshots(NIGHT))
            while not entered.is_set():
                await asyncio.sleep(0.005)
            # jetzt startet jemand von Hand (wie die Route: geprüft, dann Start)
            assert not sv.sync_running("wlo-prod")
            sv.start_sync("wlo-prod", "full")
            manual = sv.syncs["wlo-prod"]
            release.set()
            started = await night
            second = sv.syncs["wlo-prod"]
            both = second is not manual
            await asyncio.gather(manual, second)
            return started, both, status(snap, False)
        finally:
            await sv.aclose()

    started, both, s = asyncio.run(go())
    assert not both, "zwei Abzüge derselben Instanz liefen gleichzeitig"


# C: Zeitumstellung im Herbst mit Uhrzeit in der doppelten Stunde
def test_fall_back_runs_twice(tmp_path):
    repo = DataRepo(make_docs(40))

    async def go():
        sv = mk(tmp_path, repo, nightly="02:30")
        try:
            sv.start_sync("wlo-prod", "full")
            await sv.syncs["wlo-prod"]
            snap = sv.snapshot("wlo-prod")
            snap.set_state(finished_at=datetime(2026, 10, 24, 12, 0, tzinfo=UTC).timestamp())
            out = []
            # 25.10.2026: 02:40 MESZ = 00:40 UTC, 02:40 MEZ = 01:40 UTC
            for when in (
                datetime(2026, 10, 25, 0, 40, tzinfo=UTC),
                datetime(2026, 10, 25, 1, 40, tzinfo=UTC),
                datetime(2026, 10, 25, 5, 0, tzinfo=UTC),
            ):
                got = await sv.nightly_snapshots(when)
                if got:
                    await sv.syncs["wlo-prod"]
                    # die Uhr des Abzugs ist die echte; auf die gespielte Zeit zurückstellen
                    snap.set_state(finished_at=when.timestamp())
                out.append(got)
            return out
        finally:
            await sv.aclose()

    out = asyncio.run(go())
    assert sum(map(len, out)) == 1, out


def test_spring_forward(tmp_path):
    repo = DataRepo(make_docs(40))

    async def go():
        sv = mk(tmp_path, repo, nightly="02:30")
        try:
            sv.start_sync("wlo-prod", "full")
            await sv.syncs["wlo-prod"]
            snap = sv.snapshot("wlo-prod")
            snap.set_state(finished_at=datetime(2026, 3, 28, 12, 0, tzinfo=UTC).timestamp())
            out = []
            # 29.03.2026: 01:59 MEZ = 00:59 UTC; 03:00 MESZ = 01:00 UTC
            for when in (
                datetime(2026, 3, 29, 0, 20, tzinfo=UTC),
                datetime(2026, 3, 29, 0, 59, tzinfo=UTC),
                datetime(2026, 3, 29, 1, 1, tzinfo=UTC),
                datetime(2026, 3, 29, 1, 5, tzinfo=UTC),
                datetime(2026, 3, 29, 1, 15, tzinfo=UTC),
                datetime(2026, 3, 29, 1, 29, tzinfo=UTC),
                datetime(2026, 3, 29, 1, 31, tzinfo=UTC),
                datetime(2026, 3, 29, 9, 0, tzinfo=UTC),
            ):
                got = await sv.nightly_snapshots(when)
                if got:
                    await sv.syncs["wlo-prod"]
                    # die Uhr des Abzugs ist die echte; auf die gespielte Zeit zurückstellen
                    snap.set_state(finished_at=when.timestamp())
                out.append(got)
            return out
        finally:
            await sv.aclose()

    out = asyncio.run(go())
    assert sum(map(len, out)) == 1, out


# C: kaputte Datei einer Instanz hält die anderen auf
def test_one_broken_file_blocks_the_rest(tmp_path):
    repo = DataRepo(make_docs(40))

    async def go():
        sv = mk(tmp_path, repo)
        try:
            ids = [i.id for i in sv.registry.list()]
            last = ids[-1]
            sv.start_sync(last, "full")
            await sv.syncs[last]
            sv.snapshot(last).set_state(finished_at=(NIGHT - timedelta(hours=12)).timestamp())
            (tmp_path / "abzug" / f"{ids[0]}.sqlite3").write_bytes(b"kein sqlite " * 500)
            try:
                return await sv.nightly_snapshots(NIGHT)
            except Exception as e:
                return type(e).__name__
        finally:
            await sv.aclose()

    out = asyncio.run(go())
    assert isinstance(out, list) and out


# C: Fehler in also bricht den Planer nicht
def test_also_error_keeps_scheduler():
    from wda.scheduler import Scheduler

    calls = {"n": 0}

    class S:
        async def due(self, now):
            return []

    async def also(now):
        calls["n"] += 1
        raise RuntimeError("x")

    async def sleep(_):
        if calls["n"] >= 3:
            raise asyncio.CancelledError

    sch = Scheduler(lambda: S(), lambda: None, sleep=sleep, also=also)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(sch.run_forever())
    assert calls["n"] == 3


def make_api(tmp_path, repo, page=10):
    settings = Settings(
        admin_password=ADMIN_PASSWORD,
        cookie_secure=False,
        data_dir=tmp_path,
        snapshot_page=page,
        instances="[]",
        scheduler=False,
    )
    c = TestClient(
        create_app(settings, transport=httpx.MockTransport(lambda r: repo.handler(r))),
        raise_server_exceptions=False,
    )
    c.__enter__()
    assert c.post("/api/auth/login", json={"password": ADMIN_PASSWORD}).status_code == 204
    return c, settings


def wait(api, instance="wlo-prod"):
    for _ in range(1000):
        s = api.get(f"/api/snapshot/{instance}").json()
        if s["status"] != "running":
            return s
        time.sleep(0.02)
    raise AssertionError("nicht fertig")


def test_stop_during_clear(tmp_path):
    repo = DataRepo(make_docs(30))
    api, _ = make_api(tmp_path, repo)
    try:
        api.post("/api/snapshot/wlo-prod/start", json={})
        wait(api)
        snap = api.app.state.services.snapshot("wlo-prod")
        real = snap.clear
        entered, release = threading.Event(), threading.Event()

        def slow():
            entered.set()
            release.wait(5)
            real()

        snap.clear = slow
        out = {}
        t = threading.Thread(
            target=lambda: out.setdefault("clear", api.delete("/api/snapshot/wlo-prod"))
        )
        t.start()
        assert entered.wait(5)
        api.post("/api/snapshot/wlo-prod/start", json={})
        api.post("/api/snapshot/wlo-prod/stop")
        r1 = api.post(
            "/api/snapshot/wlo-prod/start", json={}
        )  # nach stop gilt die Sperre nicht mehr
        release.set()
        t.join()
        wait(api)
        assert out["clear"].status_code == 200
        assert r1.status_code == 409
    finally:
        api.__exit__(None, None, None)


def test_start_answer_after_failure(tmp_path):
    repo = DataRepo(make_docs(60))
    api, _ = make_api(tmp_path, repo)
    try:
        real, calls = repo.handler, {"n": 0}

        def h(request):
            if request.url.params.get("maxItems"):
                calls["n"] += 1
                if calls["n"] == 3:
                    return httpx.Response(400, json={"error": "x", "message": "kaputt"})
            return real(request)

        repo.handler = h
        api.post("/api/snapshot/wlo-prod/start", json={})
        s = wait(api)
        assert s["status"] == "failed"
        r = api.post("/api/snapshot/wlo-prod/start", json={}).json()
        wait(api)
        assert r["status"] == "running" and r["error"] is None
    finally:
        api.__exit__(None, None, None)


def test_double_start(tmp_path):
    repo = DataRepo(make_docs(60))
    real = repo.handler

    def slow(request):
        if request.url.params.get("maxItems"):
            time.sleep(0.05)
        return real(request)

    repo.handler = slow
    api, _ = make_api(tmp_path, repo)
    try:
        codes = []
        ts = [
            threading.Thread(
                target=lambda: codes.append(
                    api.post("/api/snapshot/wlo-prod/start", json={}).status_code
                )
            )
            for _ in range(6)
        ]
        [t.start() for t in ts]
        [t.join() for t in ts]
        assert sorted(codes).count(202) == 1
        assert wait(api)["count"] == 60
    finally:
        api.__exit__(None, None, None)
