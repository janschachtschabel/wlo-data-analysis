import json

import pytest
from fastapi.testclient import TestClient

from wda.api.export import file_name
from wda.main import create_app
from wda.report.formats import pdf_available

from .conftest import ADMIN_PASSWORD
from .esdata import DataRepo
from .test_definition_routes import QUERY, make


@pytest.fixture
def api(settings):
    with TestClient(create_app(settings, transport=DataRepo().transport())) as client:
        assert client.post("/api/auth/login",
                           json={"password": ADMIN_PASSWORD}).status_code == 204
        yield client


def test_file_names_are_harmless():
    assert file_name("Fächer & Größen: Ü/1", "2026-11-13T19:50:15Z", "pdf") == (
        "Facher-Groen-U-1-2026-11-13.pdf")
    assert file_name('../../etc/passwd"\r\nX: y', "kaputt", "csv") == "etc-passwd-X-y-lauf.csv"
    assert file_name("日本語", "2026-01-01", "md") == "bericht-2026-01-01.md"
    assert len(file_name("x" * 500, "2026-01-01", "html")) < 80


def test_export_in_every_format(api):
    m = make(api, "metric", 'Fächer "nach" Fach', {"query": QUERY, "view": {"type": "bar"}})
    run = api.post(f"/api/definitions/{m['id']}/run").json()
    expected = {"json": "application/json", "csv": "text/csv", "md": "text/markdown",
                "html": "text/html", "pdf": "application/pdf"}
    if not pdf_available():
        del expected["pdf"]
        r = api.get(f"/api/runs/{run['id']}/export", params={"format": "pdf"})
        assert r.status_code == 503 and "Pango" in r.json()["detail"]
    for fmt, media in expected.items():
        r = api.get(f"/api/runs/{run['id']}/export", params={"format": fmt})
        assert r.status_code == 200, r.text
        assert r.headers["content-type"].startswith(media)
        disposition = r.headers["content-disposition"]
        day = run["started_at"][:10]
        assert disposition.startswith(f'attachment; filename="Facher-nach-Fach-{day}.')
        assert "sandbox" in r.headers["content-security-policy"]
        assert r.headers["cache-control"] == "no-store"
    assert api.get(f"/api/runs/{run['id']}/export").headers["content-type"].startswith(
        "text/html")
    data = json.loads(api.get(f"/api/runs/{run['id']}/export?format=json").content)
    assert data["blocks"][0]["rows"][-1] == {
        "key": "__total__", "label": "Gesamt", "kind": "total", "values": [400], "level": 0,
        "percent": None}
    if pdf_available():
        pdf = api.get(f"/api/runs/{run['id']}/export?format=pdf").content
        assert pdf.startswith(b"%PDF")
    inline = api.get(f"/api/runs/{run['id']}/export", params={"download": "false"})
    assert inline.headers["content-disposition"].startswith("inline")


def test_export_errors_and_login(api):
    assert api.get("/api/runs/gibt-es-nicht/export").status_code == 404
    m = make(api, "metric", "M", {"query": QUERY})
    run = api.post(f"/api/definitions/{m['id']}/run").json()
    r = api.get(f"/api/runs/{run['id']}/export", params={"format": "docx"})
    assert r.status_code == 422 and r.json()["kind"] == "invalid_request"
    api.cookies.clear()
    assert api.get(f"/api/runs/{run['id']}/export").status_code == 401


@pytest.mark.parametrize("damage", [
    "UPDATE run_results SET result='{kaputt'",
    "UPDATE run_results SET result='{\"schema_version\": 1}'",
    "UPDATE run_results SET result='[1, 2]'",
    "UPDATE run_results SET query='null'",
    "UPDATE run_results SET error='\"Text\"', result=NULL",
    "UPDATE run_results SET result=json_set(result, '$.rows[0].values', json('[]'))",
    "UPDATE run_results SET result=json_set(result, '$.rows[0].values[0]', '=1+1')",
    "UPDATE runs SET spec='{kaputt'",
])
def test_damaged_runs_give_a_clear_answer_never_500(api, settings, damage):
    import sqlite3
    m = make(api, "metric", "M", {"query": QUERY})
    run = api.post(f"/api/definitions/{m['id']}/run").json()
    path = settings.data_dir / "store.sqlite3"
    con = sqlite3.connect(path)
    con.execute(damage)
    con.commit()
    con.close()
    for url in [f"/api/runs/{run['id']}"] + [
            f"/api/runs/{run['id']}/export?format={f}" for f in ("json", "csv", "md", "html")]:
        r = api.get(url)
        assert r.status_code in (200, 422), (url, r.status_code, r.text)
        if r.status_code == 422:
            assert r.json()["kind"] == "damaged_run"
        elif "export" in url:
            assert b"=1+1" not in r.content or b"'=1+1" in r.content
