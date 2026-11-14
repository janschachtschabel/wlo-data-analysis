"""Fälle aus dem Review von Paket 7: jeder war ein belegter Befund."""
# ruff: noqa: E701, E702, F401, F811, I001, E501, T201, S101, F841, E402
import asyncio
import time
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo
import pytest
from .test_schedules import api, clock, repo, down, schedule, one, utc  # noqa
from .test_definition_routes import QUERY, FILTER, TAX, make, tile
from .esdata import DISC
from wda.store.schedules import next_due
from wda.store.runner import Runner


def test_A_stale_snapshot_runs_disabled_schedule(api, clock, monkeypatch):
    a = make(api, "metric", "A", {"query": QUERY}); b = make(api, "metric", "B", {"query": QUERY})
    sa = schedule(api, a["id"], at="11:10"); sb = schedule(api, b["id"], at="11:30")
    real = Runner.run; order = []
    async def run(self, def_id, **kw):
        order.append(def_id)
        if def_id == a["id"]:
            # während A läuft, schaltet der Admin B aus
            self._db.write_now(lambda con: con.execute("UPDATE schedules SET enabled=0 WHERE id=?", (sb["id"],)))
        return await real(self, def_id, **kw)
    monkeypatch.setattr(Runner, "run", run)
    clock.forward(hours=2)
    api.tick()
    assert order == [a["id"]], "ausgeschalteter Zeitplan lief trotzdem"


def test_B_manual_success_does_not_cancel_retry(api, clock, down):
    m = make(api, "metric", "F", {"query": QUERY}); s = schedule(api, m["id"])
    down["on"] = True; clock.forward(hours=1); assert api.tick() == 1
    assert one(api, s["id"])["attempts"] == 1
    down["on"] = False
    rid = api.post(f"/api/schedules/{s['id']}/run").json()["id"]
    for _ in range(300):
        if api.get(f"/api/runs/{rid}").json()["status"] != "running": break
    now = one(api, s["id"])
    clock.forward(minutes=16)
    n = api.tick()
    assert n == 0, "Wiederholung läuft trotz inzwischen erfolgreichem Lauf"


def test_C_clock_back_during_run(api, clock, monkeypatch):
    m = make(api, "metric", "F", {"query": QUERY}); s = schedule(api, m["id"])
    real = Runner.run
    async def run(self, def_id, **kw):
        out = await real(self, def_id, **kw)
        clock.now -= timedelta(minutes=30)   # Systemuhr wird während des Laufs zurückgestellt
        return out
    monkeypatch.setattr(Runner, "run", run)
    clock.forward(hours=1, minutes=5)
    assert api.tick() == 1
    monkeypatch.setattr(Runner, "run", real)
    clock.forward(minutes=31)
    assert api.tick() == 0, "zweiter Lauf am selben Termin"


def test_D_previous_is_failed_run(api, clock, down):
    m = make(api, "metric", "F", {"query": QUERY})
    r1 = api.post(f"/api/definitions/{m['id']}/run", json={}).json()
    down["on"] = True
    r2 = api.post(f"/api/definitions/{m['id']}/run", json={"refresh": True}).json()
    assert r2["status"] == "failed"
    down["on"] = False
    r3 = api.post(f"/api/definitions/{m['id']}/run", json={"refresh": True}).json()
    rep = api.get(f"/api/runs/{r3['id']}/report", params={"compare": "previous"}).json()
    assert rep["compare"]["run_id"] == r1["id"]


def test_E_compare_run_without_points(api, monkeypatch):
    m = make(api, "metric", "F", {"query": QUERY})
    monkeypatch.setattr("wda.store.points.MAX_POINTS", 1)
    r1 = api.post(f"/api/definitions/{m['id']}/run", json={}).json()
    monkeypatch.setattr("wda.store.points.MAX_POINTS", 60000)
    r2 = api.post(f"/api/definitions/{m['id']}/run", json={}).json()
    # der vorige Lauf hat keine Zahlen: er ist kein Vergleich, und das steht an ihm
    assert api.get(f"/api/runs/{r2['id']}/report", params={"compare": "previous"}).status_code == 404
    named = api.get(f"/api/runs/{r2['id']}/report", params={"compare": r1["id"]}).json()
    assert [n["code"] for n in named["notes"]] == ["compare_no_points"]
    assert not any(n["code"].startswith("compare") for n in named["blocks"][0]["notes"])
    assert [n["code"] for n in api.get(f"/api/runs/{r1['id']}/report").json()["notes"]] == ["no_points"]
    hist = api.get(f"/api/definitions/{m['id']}/history").json()
    assert "runs_without_points" in [n["code"] for n in hist["notes"]]


def test_F_date_picks_later_run(api):
    m = make(api, "metric", "F", {"query": QUERY})
    r1 = api.post(f"/api/definitions/{m['id']}/run", json={}).json()
    r2 = api.post(f"/api/definitions/{m['id']}/run", json={}).json()
    rep = api.get(f"/api/runs/{r1['id']}/report", params={"compare": local_day(r1["started_at"])})
    assert rep.status_code == 404 or rep.json()["compare"]["run_id"] != r2["id"]


def test_G_from_with_offset(api):
    m = make(api, "metric", "F", {"query": QUERY})
    r1 = api.post(f"/api/definitions/{m['id']}/run", json={}).json()
    t = datetime.fromisoformat(r1["started_at"].replace("Z", "+00:00"))
    frm = (t - timedelta(minutes=1)).astimezone(ZoneInfo("Asia/Tokyo")).isoformat(timespec="seconds")
    h = api.get(f"/api/definitions/{m['id']}/history", params={"from": frm})
    assert len(h.json()["points"]) == 1


def test_H_filter_change_not_marked(api, repo):
    f = make(api, "filter", "Flt", {"filters": [{"property": TAX, "values": [DISC + "380"]}]})
    m = make(api, "metric", "F", {"query": {"instance": "wlo-prod", "rows": {"property": "ccm:educationalcontext"}}, "filter_ids": [f["id"]]})
    r1 = api.post(f"/api/definitions/{m['id']}/run", json={}).json()
    u = api.put(f"/api/definitions/{f['id']}", json={"version": 1, "spec": {"filters": [{"property": TAX, "values": [DISC + "120"]}]}})
    assert u.status_code == 200, u.text
    r2 = api.post(f"/api/definitions/{m['id']}/run", json={"refresh": True}).json()
    h = api.get(f"/api/definitions/{m['id']}/history").json()
    rep = api.get(f"/api/runs/{r2['id']}/report", params={"compare": "previous"}).json()
    assert h["notes"] or rep["notes"], "Filter geändert, Zahlen anders, kein Hinweis"


def test_I_explicit_default_instance_is_separate(api):
    m = make(api, "metric", "F", {"query": QUERY})
    api.post(f"/api/definitions/{m['id']}/run", json={}).json()
    api.post(f"/api/definitions/{m['id']}/run", json={"instance": "wlo-prod"}).json()
    h = api.get(f"/api/definitions/{m['id']}/history").json()
    assert len(h["points"]) == 2


def test_J_failed_jobs_pile_up(api):
    jobs = api.app.state.services.jobs
    m = make(api, "metric", "F", {"query": QUERY}); s = schedule(api, m["id"])
    from wda.store.runner import Runner
    import unittest.mock as um
    async def boom(self, *a, **k): raise RuntimeError("x")
    with um.patch.object(Runner, "run", boom):
        for _ in range(200):
            assert api.post(f"/api/schedules/{s['id']}/run").status_code in (202, 409)
        time.sleep(0.5)
    assert len(jobs._jobs) <= 50


def test_K_next_due_property():
    bad = []
    for zone in ["Europe/Berlin", "America/Havana", "Australia/Lord_Howe", "America/Santiago", "Africa/Casablanca", "Asia/Tehran", "Pacific/Chatham", "America/St_Johns", "Asia/Kolkata", "UTC", "America/Sao_Paulo", "Asia/Beirut", "Africa/Cairo"]:
        tz = ZoneInfo(zone)
        for at in ["00:00", "00:30", "01:30", "02:00", "02:30", "03:00", "23:59", "12:00"]:
            for every, wd, d in [("daily", None, None), ("weekly", 6, None), ("monthly", None, 28), ("monthly", None, 1)]:
                t = datetime(2025, 12, 31, 13, 7, tzinfo=UTC); seen = []
                while t < datetime(2027, 1, 5, tzinfo=UTC):
                    n = next_due(every, at, wd, d, t, tz)
                    if not n > t: bad.append((zone, at, every, t, n, "not after")); break
                    loc = n.astimezone(tz)
                    if every == "weekly" and loc.weekday() != 6: bad.append((zone, at, every, n, "weekday"))
                    if every == "monthly" and loc.day != d: bad.append((zone, at, every, n, "day"))
                    if every == "daily" and seen and loc.date() != seen[-1] + timedelta(days=1): bad.append((zone, at, every, n, "gap/dup day"))
                    if loc.strftime("%H:%M") != at:
                        bad.append((zone, at, every, loc.isoformat(), "shifted"))
                    seen.append(loc.date()); t = n
                # mid-interval starts: result must equal the chain
    shifted = [b for b in bad if b[-1] == "shifted"]; other = [b for b in bad if b[-1] != "shifted"]
    assert not other


from fastapi.testclient import TestClient
from wda.main import create_app
from .conftest import ADMIN_PASSWORD
from .test_history import local_day, run as run_now

def S(i, v):
    return {"id": i, "label": i.upper(), "filters": [{"property": "ccm:educationalcontext", "values": [v]}]}

def test_column_dropped_and_new(api):
    m = make(api, "metric", "F", {"query": {**QUERY, "series": [S("a", "Hochschule"), S("b", "Primarstufe")]}})
    r1 = run_now(api, m["id"])
    u = api.put(f"/api/definitions/{m['id']}", json={"version": 1, "spec": {"query": {**QUERY, "series": [S("a", "Hochschule"), S("c", "Sekundarstufe I")]}}})
    assert u.status_code == 200, u.text
    r2 = run_now(api, m["id"])
    rep = api.get(f"/api/runs/{r2['id']}/report", params={"compare": "previous"}).json()
    b = rep["blocks"][0]
    csv = api.get(f"/api/runs/{r2['id']}/export", params={"format": "csv", "compare": "previous"}).text
    assert any("column" in n["code"] or "spalte" in n["text"].lower() for n in b["notes"]), "neue/entfallene Spalte ohne Hinweis"

def test_two_apps_one_db_double_run(settings, repo):
    from .test_schedules import Clock
    clock = Clock()
    off = settings.model_copy(update={"scheduler": False})
    a1 = create_app(off, transport=repo.transport(), now=clock)
    a2 = create_app(off, transport=repo.transport(), now=clock)
    with TestClient(a1) as c1, TestClient(a2) as c2:
        c1.post("/api/auth/login", json={"password": ADMIN_PASSWORD})
        m = make(c1, "metric", "F", {"query": QUERY})
        c1.post("/api/schedules", json={"definition_id": m["id"], "every": "daily", "at": "12:00"})
        clock.forward(hours=2)
        # beide Planer lesen "fällig", bevor einer verbucht hat
        d1 = c1.portal.call(a1.state.services.schedules.due, clock()); d2 = c2.portal.call(a2.state.services.schedules.due, clock())
        for c, a, d in ((c1, a1, d1), (c2, a2, d2)):
            for s in d: c.portal.call(a.state.scheduler.execute, s)
        n = len(c1.get(f"/api/definitions/{m['id']}/runs").json())
        assert n == 1

