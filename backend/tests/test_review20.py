"""Befunde aus dem Review von Paket 20: Versand vergleicht mit dem letzten Lauf des Zeitplans,
der Lesezugang endet mit seinem Passwort, geerbte Zielwerte, leere Ausblend-Einträge."""
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from wda.config import Settings
from wda.engine.spec import QuerySpec
from wda.main import create_app

from .conftest import ADMIN_PASSWORD
from .esdata import DataRepo
from .test_definition_routes import QUERY, make
from .test_engine import TAX
from .test_send import api, execute, repo, schedule  # noqa: F401

VIEWER = "nur-lesen-4711"


def _nightly(api, def_id):  # noqa: F811
    async def go():
        return await api.app.state.services.runner.run(def_id, refresh=True, origin="nightly")

    return api.portal.call(go)


def test_only_notable_compares_with_the_schedules_last_run(api, repo):  # noqa: F811
    m = make(api, "metric", "Ruhig", {"query": QUERY})
    item = schedule(api, m["id"], {"to": ["a@example.org"], "only_notable": True})
    item = execute(api, item)
    assert item["last_send"] == "Nichts Auffälliges, nicht verschickt."
    repo.docs.extend([dict(d) for d in repo.docs[:200]])
    _nightly(api, m["id"])  # ein Lauf dazwischen sieht die Änderung zuerst
    after = execute(api, item)
    assert after["last_send"] == "Verschickt an 1 Empfänger."
    body = api.mails[0].get_body(("plain",)).get_content()
    assert "Auffällige Veränderungen seit dem Lauf vom" in body


def test_only_notable_works_for_another_instance(api, repo):  # noqa: F811
    m = make(api, "metric", "Staging", {"query": QUERY})
    out = api.post("/api/schedules", json={
        "definition_id": m["id"], "instance": "wlo-staging", "every": "daily", "at": "06:00",
        "send": {"to": ["a@example.org"], "only_notable": True}})
    assert out.status_code == 201, out.text
    item = execute(api, out.json())
    assert not api.mails
    repo.docs.extend([dict(d) for d in repo.docs[:200]])
    assert execute(api, item)["last_send"] == "Verschickt an 1 Empfänger."


def test_mail_has_date_and_message_id(api):  # noqa: F811
    m = make(api, "metric", "Kopf", {"query": QUERY})
    execute(api, schedule(api, m["id"], {"to": ["a@example.org"]}))
    assert api.mails[0]["Date"] and api.mails[0]["Message-ID"].endswith("@example.org>")


def test_put_without_send_keeps_it(api):  # noqa: F811
    m = make(api, "metric", "Bleibt", {"query": QUERY})
    item = schedule(api, m["id"], {"to": ["a@example.org"]})
    body = {"every": "daily", "at": "07:00", "enabled": False}
    assert api.put(f"/api/schedules/{item['id']}", json=body).json()["send"]["to"] == [
        "a@example.org"]
    assert api.put(f"/api/schedules/{item['id']}", json={**body, "send": None}).json()[
        "send"] is None


@pytest.mark.parametrize("url", [
    "http://169.254.169.254/latest", "http://127.0.0.1:8000/x", "http://localhost/x",
    "http://10.0.0.5/x", "http://app-intern:9000/", "http://[::1]/x"])
def test_webhook_refuses_internal_targets(api, url):  # noqa: F811
    m = make(api, "metric", "Innen", {"query": QUERY})
    out = api.post("/api/schedules", json={"definition_id": m["id"], "every": "daily",
                                           "at": "06:00", "send": {"webhook": url}})
    assert out.status_code == 422 and "Webhook" in out.text


def test_inherited_target_shows_on_highlights(api):  # noqa: F811
    goal = make(api, "metric", "Mit Ziel", {
        "query": {"instance": "wlo-prod"},
        "view": {"type": "number", "options": {"target": 100000}}})
    board = api.post("/api/definitions", json={"kind": "dashboard", "name": "Dash", "spec": {
        "tiles": [{"id": "a", "title": "Kachel", "metric_id": goal["id"],
                   "layout": {"x": 0, "y": 0, "w": 6, "h": 4}}]}})
    assert board.status_code == 201, board.text
    assert api.post(f"/api/definitions/{board.json()['id']}/run").status_code < 300
    targets = api.get("/api/highlights").json()["targets"]
    assert [t["name"] for t in targets] == ["Dash"]


def test_blank_exclude_is_refused():
    with pytest.raises(ValidationError):
        QuerySpec.model_validate({"instance": "wlo-prod",
                                  "rows": {"property": TAX, "exclude": [" "]}})


def _app(tmp_path, **more):
    return create_app(Settings(admin_password=ADMIN_PASSWORD, cookie_secure=False,
                               data_dir=tmp_path, scheduler=False, **more),
                      transport=DataRepo().transport())


@pytest.mark.parametrize("later", [{}, {"viewer_password": "ganz-anderes-passwort"}])
def test_viewer_session_ends_with_its_password(tmp_path, later):
    with TestClient(_app(tmp_path, viewer_password=VIEWER)) as client:
        assert client.post("/api/auth/login", json={"password": VIEWER}).status_code == 204
        cookie = client.cookies.get("wda_session")
        assert client.get("/api/runs").status_code == 200
    with TestClient(_app(tmp_path, **later)) as client:
        client.cookies.set("wda_session", cookie)
        assert client.get("/api/auth/status").json()["authenticated"] is False
        assert client.get("/api/runs").status_code == 401


def test_viewer_paths_have_no_side_doors(tmp_path):
    with TestClient(_app(tmp_path, viewer_password=VIEWER)) as client:
        client.post("/api/auth/login", json={"password": ADMIN_PASSWORD})
        m = make(client, "metric", "F", {"query": QUERY})
        run = client.post(f"/api/definitions/{m['id']}/run").json()["id"]
        client.cookies.clear()
        client.post("/api/auth/login", json={"password": VIEWER})
        for method, path in [
                ("GET", "/api//definitions"), ("GET", "/api/definitions/"),
                ("GET", f"/api/runs/{run}/"),
                ("GET", "/api/runs/..%2Fschedules"), ("GET", "/api/runs/%2e%2e/schedules"),
                ("HEAD", "/api/schedules"), ("OPTIONS", "/api/schedules"),
                ("OPTIONS", f"/api/runs/{run}"), ("DELETE", f"/api/runs/{run}"),
                ("PUT", f"/api/definitions/{m['id']}"), ("POST", "/api/assistant/comment")]:
            out = client.request(method, path)
            assert out.status_code in (403, 404, 405), (method, path, out.status_code)
            assert "wda_session" not in out.text


def test_precompute_can_be_switched_off(tmp_path):
    for word in ("off", "aus", "OFF "):
        with TestClient(_app(tmp_path, precompute=word)) as client:
            assert client.app.state.services.precompute == []


def test_init_accepts_punycode_endings(tmp_path, monkeypatch):
    from wda import init
    monkeypatch.setenv("DOMAIN", "daten.example.xn--p1ai")
    monkeypatch.setenv("WDA_DATA_DIR", str(tmp_path))
    assert init._HOST.fullmatch("daten.example.xn--p1ai")
    assert not init._HOST.fullmatch("https://daten.example.org")


def test_only_notable_reports_a_partial_run(api, repo, monkeypatch):  # noqa: F811
    from wda import notify
    m = make(api, "metric", "Lücke", {"query": QUERY})
    item = execute(api, schedule(api, m["id"], {"to": ["a@example.org"], "only_notable": True}))
    assert not api.mails
    real = notify.build_report

    def partial(run):
        report = real(run)
        report.status = "partial"
        return report

    monkeypatch.setattr(notify, "build_report", partial)
    assert execute(api, item)["last_send"] == "Verschickt an 1 Empfänger."
    assert "(auffällig)" in api.mails[0]["Subject"]


def test_precompute_does_not_hold_the_planner(tmp_path):
    import asyncio
    from datetime import UTC, datetime

    with TestClient(_app(tmp_path)) as client:
        services = client.app.state.services
        started = asyncio.Event()

        async def slow(_now):
            started.set()
            await asyncio.sleep(30)

        services.nightly_precompute = slow

        async def go():
            await client.app.state.scheduler._also(datetime(2026, 11, 14, 4, tzinfo=UTC))
            await client.app.state.scheduler._also(datetime(2026, 11, 14, 4, tzinfo=UTC))
            await asyncio.wait_for(started.wait(), 2)
            return len(client.app.state.precomputing)

        assert client.portal.call(go) == 1  # läuft daneben, und nur einmal


def test_docx_marks_cut_text_and_keeps_numbered_items():
    from wda.report.docx import _markdown, _run
    assert "(gekürzt)" in _run("x" * 9000) and "(gekürzt)" not in _run("x" * 100)
    assert len(_markdown("1. eins\n2. zwei\n\n| a | b |\n|---|---|\n| 1 | 2 |")) == 4


def test_schema_2_gains_the_send_columns(tmp_path):
    import sqlite3

    with TestClient(_app(tmp_path)) as client:
        client.post("/api/auth/login", json={"password": ADMIN_PASSWORD})
        m = make(client, "metric", "Alt", {"query": QUERY})
        assert client.post("/api/schedules", json={
            "definition_id": m["id"], "every": "daily", "at": "06:00"}).status_code == 201
    [path] = [p for p in tmp_path.iterdir() if p.suffix.startswith(".sqlite") or p.suffix == ".db"]
    con = sqlite3.connect(path)
    con.execute("ALTER TABLE schedules DROP COLUMN send")
    con.execute("ALTER TABLE schedules DROP COLUMN last_send")
    con.execute("UPDATE meta SET value='2' WHERE key='schema_version'")
    con.commit()
    con.close()
    with TestClient(_app(tmp_path)) as client:
        client.post("/api/auth/login", json={"password": ADMIN_PASSWORD})
        [item] = client.get("/api/schedules").json()
        assert item["send"] is None and item["last_send"] is None
        out = client.put(f"/api/schedules/{item['id']}", json={
            "every": "daily", "at": "07:00", "send": {"to": ["a@example.org"]}})
        assert out.json()["send"]["to"] == ["a@example.org"]


def test_run_list_names_the_definition_version(api):  # noqa: F811
    m = make(api, "metric", "Fassung", {"query": QUERY})
    api.post(f"/api/definitions/{m['id']}/run")
    [head] = api.get(f"/api/definitions/{m['id']}/runs?limit=5").json()
    assert head["definition_version"] == api.get(f"/api/definitions/{m['id']}").json()["version"]
