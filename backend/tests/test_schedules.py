"""Zeitpläne: Termine, Ablage, Planer und Routen."""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
import pytest
from fastapi.testclient import TestClient

from wda.main import create_app
from wda.store.schedules import next_due

from .conftest import ADMIN_PASSWORD
from .esdata import DataRepo
from .esfake import SEARCH_PATH
from .test_definition_routes import FILTER, QUERY, make

BERLIN = ZoneInfo("Europe/Berlin")


def utc(*parts):
    return datetime(*parts, tzinfo=UTC)


def test_next_due_daily_weekly_monthly():
    after = utc(2026, 11, 13, 10, 0)  # Freitag, 11:00 in Berlin
    assert next_due("daily", "12:00", None, None, after, BERLIN) == utc(2026, 11, 13, 11, 0)
    assert next_due("daily", "06:00", None, None, after, BERLIN) == utc(2026, 11, 14, 5, 0)
    # genau zum Termin: der nächste, nicht derselbe
    assert next_due("daily", "11:00", None, None, after, BERLIN) == utc(2026, 11, 14, 10, 0)
    assert next_due("weekly", "06:00", 0, None, after, BERLIN) == utc(2026, 11, 16, 5, 0)
    assert next_due("weekly", "12:00", 4, None, after, BERLIN) == utc(2026, 11, 13, 11, 0)
    assert next_due("monthly", "06:00", None, 1, after, BERLIN) == utc(2026, 12, 1, 5, 0)
    assert next_due("monthly", "06:00", None, 28, after, BERLIN) == utc(2026, 11, 28, 5, 0)


def test_next_due_across_daylight_saving():
    # 29.03.2026: 02:30 gibt es nicht, der Lauf rückt hinter die Lücke
    spring = next_due("daily", "02:30", None, None, utc(2026, 3, 28, 12, 0), BERLIN)
    assert spring == utc(2026, 3, 29, 1, 30)
    assert spring.astimezone(BERLIN).strftime("%H:%M") == "03:30"
    # danach wieder zur gewohnten Ortszeit
    assert next_due("daily", "02:30", None, None, spring, BERLIN) == utc(2026, 3, 30, 0, 30)
    # 25.10.2026: 02:30 gibt es zweimal, der Lauf kommt einmal
    first = next_due("daily", "02:30", None, None, utc(2026, 10, 24, 12, 0), BERLIN)
    assert first == utc(2026, 10, 25, 0, 30)
    assert next_due("daily", "02:30", None, None, first, BERLIN) == utc(2026, 10, 26, 1, 30)
    # die Ortszeit bleibt über den Wechsel gleich
    assert next_due("daily", "08:00", None, None, utc(2026, 3, 28, 12, 0), BERLIN
                    ) == utc(2026, 3, 29, 6, 0)


class Clock:
    def __init__(self):
        self.now = utc(2026, 11, 13, 10, 0)

    def __call__(self):
        return self.now

    def forward(self, **delta):
        self.now += timedelta(**delta)


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def repo():
    return DataRepo()


@pytest.fixture
def down():
    return {"on": False, "boom": False}


@pytest.fixture
def api(settings, repo, clock, down):
    def handler(request: httpx.Request) -> httpx.Response:
        if down["boom"] and request.url.path == SEARCH_PATH:
            raise RuntimeError("kaputt")
        if down["on"] and request.url.path == SEARCH_PATH:
            return httpx.Response(400, json={"error": "x", "message": "geht nicht"})
        return repo.handler(request)

    off = settings.model_copy(update={"scheduler": False})
    app = create_app(off, transport=httpx.MockTransport(handler), now=clock)
    with TestClient(app) as client:
        assert client.post("/api/auth/login",
                           json={"password": ADMIN_PASSWORD}).status_code == 204
        client.tick = lambda: client.portal.call(app.state.scheduler.tick)
        yield client


def schedule(api, def_id, **more):
    body = {"definition_id": def_id, "every": "daily", "at": "12:00", **more}
    r = api.post("/api/schedules", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def one(api, sid):
    return next(s for s in api.get("/api/schedules").json() if s["id"] == sid)


def test_crud_and_rules(api):
    m = make(api, "metric", "Fächer", {"query": QUERY})
    s = schedule(api, m["id"])
    assert s["next_due"] == "2026-11-13T11:00:00Z" and s["enabled"] is True
    assert (s["definition_name"], s["definition_kind"]) == ("Fächer", "metric")
    assert s["last_run_id"] is None and s["attempts"] == 0

    # je Definition und Instanz einer
    again = api.post("/api/schedules", json={
        "definition_id": m["id"], "every": "daily", "at": "07:00"})
    assert again.status_code == 409
    other = schedule(api, m["id"], instance="wlo-staging")
    assert other["instance"] == "wlo-staging"
    assert {x["id"] for x in api.get(f"/api/schedules?definition={m['id']}").json()
            } == {s["id"], other["id"]}

    changed = api.put(f"/api/schedules/{s['id']}", json={
        "every": "weekly", "at": "06:00", "weekday": 0, "enabled": False})
    assert changed.status_code == 200, changed.text
    assert changed.json()["next_due"] == "2026-11-16T05:00:00Z"
    assert changed.json()["enabled"] is False and changed.json()["day"] is None

    for bad in ({"every": "weekly", "at": "06:00"}, {"every": "monthly", "at": "06:00"},
                {"every": "monthly", "at": "06:00", "day": 31},
                {"every": "daily", "at": "24:00"}, {"every": "daily", "at": "6:00"},
                {"every": "hourly", "at": "06:00"},
                {"every": "daily", "at": "06:00", "next_due": "2020-01-01T00:00:00Z"}):
        assert api.put(f"/api/schedules/{s['id']}", json=bad).status_code == 422, bad

    f = make(api, "filter", "Hochschule", FILTER)
    assert api.post("/api/schedules", json={
        "definition_id": f["id"], "every": "daily", "at": "06:00"}).status_code == 422
    assert api.post("/api/schedules", json={
        "definition_id": "gibt-es-nicht", "every": "daily", "at": "06:00"}).status_code == 404
    assert api.post("/api/schedules", json={
        "definition_id": m["id"], "instance": "nirgends", "every": "daily",
        "at": "06:00"}).status_code == 404

    assert api.delete(f"/api/schedules/{s['id']}").status_code == 204
    assert api.delete(f"/api/schedules/{s['id']}").status_code == 404
    assert api.put(f"/api/schedules/{s['id']}", json={
        "every": "daily", "at": "06:00"}).status_code == 404

    # mit der Definition verschwindet ihr Zeitplan
    assert api.delete(f"/api/definitions/{m['id']}").status_code in (200, 204)
    assert api.get("/api/schedules").json() == []


def test_limit_of_schedules(api, monkeypatch):
    monkeypatch.setattr("wda.store.schedules.MAX_SCHEDULES", 2)
    ids = [make(api, "metric", f"K{i}", {"query": QUERY})["id"] for i in range(3)]
    schedule(api, ids[0])
    schedule(api, ids[1])
    r = api.post("/api/schedules", json={
        "definition_id": ids[2], "every": "daily", "at": "06:00"})
    assert r.status_code == 409


def test_due_schedule_runs_once_and_moves_on(api, clock):
    m = make(api, "metric", "Fächer", {"query": QUERY})
    s = schedule(api, m["id"])
    assert api.tick() == 0  # noch nicht fällig
    clock.forward(hours=1)
    assert api.tick() == 1
    after = one(api, s["id"])
    assert after["last_status"] == "ok" and after["last_error"] is None
    assert after["next_due"] == "2026-11-14T11:00:00Z" and after["attempts"] == 0
    runs = api.get(f"/api/definitions/{m['id']}/runs").json()
    assert [r["id"] for r in runs] == [after["last_run_id"]]
    assert runs[0]["origin"] == "schedule"
    assert api.tick() == 0


def test_missed_dates_are_caught_up_once(api, clock):
    m = make(api, "metric", "Fächer", {"query": QUERY})
    s = schedule(api, m["id"])
    clock.forward(days=10)  # die App war zehn Tage aus
    assert api.tick() == 1
    assert api.tick() == 0
    assert len(api.get(f"/api/definitions/{m['id']}/runs").json()) == 1
    assert one(api, s["id"])["next_due"] == "2026-11-23T11:00:00Z"


def test_failed_run_is_retried_three_times(api, clock, down):
    m = make(api, "metric", "Fächer", {"query": QUERY})
    s = schedule(api, m["id"])
    down["on"] = True
    clock.forward(hours=1)  # 11:00 UTC
    expected = ["2026-11-13T11:15:00Z", "2026-11-13T11:45:00Z", "2026-11-13T12:45:00Z",
                "2026-11-14T11:00:00Z"]
    for attempt, due in enumerate(expected, start=1):
        assert api.tick() == 1
        now = one(api, s["id"])
        assert now["last_status"] == "failed" and now["last_error"]
        assert now["next_due"] == due
        assert now["attempts"] == (attempt if attempt < 4 else 0)
        assert api.tick() == 0
        clock.now = datetime.fromisoformat(due.replace("Z", "+00:00"))
    # der gescheiterte Lauf liegt wie jeder andere in der Ablage
    assert len(api.get(f"/api/definitions/{m['id']}/runs?limit=10").json()) == 4

    down["on"] = False
    assert api.tick() == 1
    now = one(api, s["id"])
    assert (now["last_status"], now["last_error"], now["attempts"]) == ("ok", None, 0)


def test_order_and_one_failure_does_not_stop_the_others(api, clock, monkeypatch):
    a = make(api, "metric", "A", {"query": QUERY})
    b = make(api, "metric", "B", {"query": QUERY})
    sa = schedule(api, a["id"], at="11:30")
    sb = schedule(api, b["id"], at="11:10")
    from wda.store.runner import Runner
    real = Runner.run
    order = []

    async def run(self, def_id, **kw):
        order.append(def_id)
        if def_id == b["id"]:
            raise RuntimeError("geheimes Detail")
        return await real(self, def_id, **kw)

    monkeypatch.setattr(Runner, "run", run)
    clock.forward(hours=2)
    assert api.tick() == 2
    assert order == [b["id"], a["id"]]  # der länger wartende zuerst
    failed = one(api, sb["id"])
    assert failed["last_status"] == "failed" and failed["last_run_id"] is None
    assert "geheimes Detail" not in failed["last_error"]
    assert failed["attempts"] == 1
    assert one(api, sa["id"])["last_status"] == "ok"


def test_disabled_schedule_and_deleted_definition(api, clock, monkeypatch):
    m = make(api, "metric", "Fächer", {"query": QUERY})
    s = schedule(api, m["id"], enabled=False)
    clock.forward(days=2)
    assert api.tick() == 0

    api.put(f"/api/schedules/{s['id']}", json={"every": "daily", "at": "12:00"})
    clock.forward(days=1)
    from wda.store.runner import Runner
    real = Runner.run

    async def run(self, def_id, **kw):
        out = await real(self, def_id, **kw)
        # die Definition verschwindet, während der Lauf noch nicht verbucht ist
        self._db.write_now(lambda con: con.execute(
            "DELETE FROM definitions WHERE id=?", (def_id,)))
        return out

    monkeypatch.setattr(Runner, "run", run)
    assert api.tick() == 1
    assert api.get("/api/schedules").json() == []


def test_run_now_keeps_the_date(api, clock):
    m = make(api, "metric", "Fächer", {"query": QUERY})
    s = schedule(api, m["id"])
    r = api.post(f"/api/schedules/{s['id']}/run")
    assert r.status_code == 202 and r.json()["status"] == "running"
    run_id = r.json()["id"]
    for _ in range(200):
        got = api.get(f"/api/runs/{run_id}").json()
        if got["status"] != "running":
            break
    assert got["status"] == "ok" and got["origin"] == "schedule" and got["saved"] is True
    now = one(api, s["id"])
    assert now["last_run_id"] == run_id and now["next_due"] == s["next_due"]
    assert api.post("/api/schedules/unbekannt/run").status_code == 404


def test_timezone_setting_is_checked(settings):
    from pydantic import ValidationError

    from wda.config import Settings
    with pytest.raises(ValidationError):
        Settings(admin_password="x", timezone="Mars/Olympus")
    assert Settings(admin_password="x", scheduler="off").scheduler is False


def test_loop_survives_a_failing_round():
    import asyncio

    from wda.scheduler import Scheduler

    rounds = []

    class Broken:
        async def due(self, now):
            rounds.append(now)
            if len(rounds) == 1:
                raise RuntimeError("Ablage gerade nicht lesbar")
            return []

    async def sleep(seconds):
        if len(rounds) >= 3:
            raise asyncio.CancelledError

    scheduler = Scheduler(lambda: Broken(), lambda: None, clock=lambda: utc(2026, 1, 1),
                          sleep=sleep)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(scheduler.run_forever())
    assert len(rounds) == 3
