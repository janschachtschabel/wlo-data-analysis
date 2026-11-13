"""Aufträge: Läufe im Hintergrund mit Fortschritt."""

import asyncio

import httpx
import pytest
from fastapi.testclient import TestClient

from wda.jobs import KEEP_FAILED, MAX_RUNNING, Jobs, TooManyJobs
from wda.main import create_app

from .conftest import ADMIN_PASSWORD
from .esdata import DataRepo
from .esfake import SEARCH_PATH
from .test_definition_routes import FILTER, QUERY, make, tile


@pytest.fixture
def repo():
    return DataRepo()


@pytest.fixture
def gate():
    return {"hold": False}


@pytest.fixture
def api(settings, repo, gate):
    async def handler(request: httpx.Request) -> httpx.Response:
        while gate["hold"] and request.url.path == SEARCH_PATH:
            await asyncio.sleep(0.01)
        return repo.handler(request)

    app = create_app(settings.model_copy(update={"scheduler": False}),
                     transport=httpx.MockTransport(handler))
    with TestClient(app) as client:
        assert client.post("/api/auth/login",
                           json={"password": ADMIN_PASSWORD}).status_code == 204
        yield client


def wait_for(api, run_id):
    for _ in range(500):
        got = api.get(f"/api/runs/{run_id}")
        if got.status_code != 200 or got.json()["status"] != "running":
            return got
    raise AssertionError("Der Auftrag wird nicht fertig")


def test_job_reports_progress_and_ends_as_stored_run(api, gate):
    d = make(api, "dashboard", "Zwei", {"tiles": [
        tile("a", query=QUERY), tile("b", query=QUERY)]})
    gate["hold"] = True
    r = api.post(f"/api/definitions/{d['id']}/run", json={"wait": False})
    assert r.status_code == 202, r.text
    job = r.json()
    assert job["status"] == "running" and job["definition_id"] == d["id"]
    running = api.get(f"/api/runs/{job['id']}").json()
    assert running["status"] == "running" and running["saved"] is False
    assert running["progress"]["done"] == 0
    # solange er läuft, steht er nicht in der Liste der Läufe und hat keinen Bericht
    assert api.get(f"/api/definitions/{d['id']}/runs").json() == []
    assert api.get(f"/api/runs/{job['id']}/report").status_code == 404

    gate["hold"] = False
    done = wait_for(api, job["id"]).json()
    assert done["status"] == "ok" and done["saved"] is True and len(done["results"]) == 2
    assert [x["id"] for x in api.get(f"/api/definitions/{d['id']}/runs").json()] == [job["id"]]
    assert api.get(f"/api/runs/{job['id']}/report").status_code == 200


def test_job_rules(api):
    m = make(api, "metric", "Fächer", {"query": QUERY})
    for bad in ({"wait": False, "save": False}, {"wait": False, "report": True}):
        assert api.post(f"/api/definitions/{m['id']}/run", json=bad).status_code == 422
    assert api.post("/api/definitions/unbekannt/run", json={"wait": False}).status_code == 404
    assert api.post(f"/api/definitions/{m['id']}/run",
                    json={"wait": False, "instance": "nirgends"}).status_code == 404
    # ein Filter lässt sich nicht ausführen: der Auftrag scheitert und sagt, warum
    f = make(api, "filter", "Hochschule", FILTER)
    r = api.post(f"/api/definitions/{f['id']}/run", json={"wait": False})
    assert r.status_code == 202
    failed = wait_for(api, r.json()["id"]).json()
    assert failed["status"] == "failed" and "Filter" in failed["error"]["detail"]


def test_too_many_jobs(api, gate):
    m = make(api, "metric", "Fächer", {"query": QUERY})
    gate["hold"] = True
    ids = []
    for _ in range(MAX_RUNNING):
        r = api.post(f"/api/definitions/{m['id']}/run", json={"wait": False})
        assert r.status_code == 202
        ids.append(r.json()["id"])
    r = api.post(f"/api/definitions/{m['id']}/run", json={"wait": False})
    assert r.status_code == 429 and r.json()["kind"] == "too_many_jobs"
    gate["hold"] = False
    for run_id in ids:
        assert wait_for(api, run_id).json()["status"] == "ok"


def test_shutdown_cancels_running_job_without_half_a_run(settings, repo):
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == SEARCH_PATH:
            await asyncio.sleep(3600)
        return repo.handler(request)

    app = create_app(settings.model_copy(update={"scheduler": False}),
                     transport=httpx.MockTransport(handler))
    with TestClient(app) as client:
        client.post("/api/auth/login", json={"password": ADMIN_PASSWORD})
        m = make(client, "metric", "Fächer", {"query": QUERY})
        assert client.post(f"/api/definitions/{m['id']}/run",
                           json={"wait": False}).status_code == 202
    with TestClient(create_app(settings.model_copy(update={"scheduler": False}),
                               transport=repo.transport())) as client:
        client.post("/api/auth/login", json={"password": ADMIN_PASSWORD})
        assert client.get("/api/runs").json() == []


def test_failed_job_expires():
    now = [0.0]
    jobs = Jobs(clock=lambda: now[0])

    async def main():
        async def boom(run_id, progress):
            progress(1, 3)
            raise RuntimeError("geheim")

        job = jobs.start("d1", boom)
        await job.task
        view = jobs.get(job.id).view()
        assert view["status"] == "failed" and view["progress"] == {"done": 1, "total": 3}
        assert "geheim" not in view["error"]["detail"]
        now[0] = KEEP_FAILED + 1
        assert jobs.get(job.id) is None

        async def fine(run_id, progress):
            return None

        ok = jobs.start("d1", fine)
        await ok.task
        assert jobs.get(ok.id) is None  # der Lauf liegt jetzt in der Ablage

        async def slow(run_id, progress):
            await asyncio.sleep(3600)

        for _ in range(MAX_RUNNING):
            jobs.start("d1", slow)
        with pytest.raises(TooManyJobs):
            jobs.start("d1", slow)
        await jobs.aclose()

    asyncio.run(main())
