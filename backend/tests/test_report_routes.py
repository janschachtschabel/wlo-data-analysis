import pytest
from fastapi.testclient import TestClient

from wda.main import create_app
from wda.report.formats import pdf_available

from .conftest import ADMIN_PASSWORD
from .esdata import DataRepo
from .test_definition_routes import QUERY, make

TEXT = "## Lage\n\n- **{{tab.total.anzahl}}** Materialien\n- <img src=x onerror=alert(1)>"


@pytest.fixture
def api(settings):
    with TestClient(create_app(settings, transport=DataRepo().transport())) as client:
        assert client.post("/api/auth/login",
                           json={"password": ADMIN_PASSWORD}).status_code == 204
        yield client


def template_spec(metric_id):
    return {"title": "Bericht", "sections": [
        {"id": "intro", "type": "text", "markdown": TEXT},
        {"id": "tab", "type": "table", "title": "Fächer", "metric_id": metric_id}]}


def test_report_of_a_saved_run(api):
    m = make(api, "metric", "M", {"query": QUERY})
    t = make(api, "template", "T", template_spec(m["id"]))
    run = api.post(f"/api/definitions/{t['id']}/run").json()
    report = api.get(f"/api/runs/{run['id']}/report").json()
    assert report["report_version"] == 1 and report["title"] == "Bericht"
    text, table = report["blocks"]
    assert text["type"] == "text" and "{{" not in text["markdown"]
    # fertiges, maskiertes HTML aus demselben Umsetzer wie der Export
    assert "<strong>400</strong>" in text["html"] and "<img" not in text["html"]
    assert "&lt;img" in text["html"]
    assert table["type"] == "table" and table["rows"][-1]["kind"] == "total"
    assert "html" not in table
    assert api.get("/api/runs/gibtsnicht/report").status_code == 404


def test_run_can_return_its_report_and_unsaved_runs_have_no_id(api):
    m = make(api, "metric", "M", {"query": QUERY})
    out = api.post(f"/api/definitions/{m['id']}/run",
                   json={"save": False, "report": True}).json()
    assert out["id"] is None and out["saved"] is False
    assert out["report"]["run_id"] is None
    assert out["report"]["blocks"][0]["rows"][-1]["values"] == [400]
    assert "report" not in api.post(f"/api/definitions/{m['id']}/run").json()
    assert api.get("/api/runs").json().__len__() == 1


def test_preview_runs_an_unsaved_definition_and_stores_nothing(api):
    m = make(api, "metric", "M", {"query": QUERY})
    r = api.post("/api/preview", json={"kind": "template", "name": "Entwurf",
                                       "spec": template_spec(m["id"])})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["run"]["id"] is None and out["run"]["status"] == "ok"
    assert out["run"]["definition_id"] is None
    assert out["report"]["title"] == "Bericht" and len(out["report"]["blocks"]) == 2
    assert api.get("/api/runs").json() == []
    assert len(api.get("/api/definitions").json()) == 1
    # ungültig: dieselbe Meldung wie beim Speichern
    bad = api.post("/api/preview", json={"kind": "metric", "spec": {"query": {}}})
    assert bad.status_code == 422 and bad.json()["kind"] == "invalid_definition"
    lost = api.post("/api/preview", json={"kind": "template",
                                          "spec": template_spec("gibtsnicht")})
    # ein fehlender Verweis ist ein Fehlerblock der Vorschau, kein Abbruch
    assert lost.json()["run"]["status"] == "failed"
    assert lost.json()["report"]["blocks"][1]["type"] == "failure"


def test_check_lists_every_problem_with_its_place(api):
    ok = api.post("/api/check", json={"kind": "metric", "spec": {"query": QUERY}})
    assert ok.status_code == 200 and ok.json() == {"ok": True, "errors": [], "hints": []}
    bad = api.post("/api/check", json={"kind": "template", "spec": {"title": "", "sections": [
        {"id": "a", "type": "text", "markdown": "x"},
        {"id": "a", "type": "gibtsnicht"}], "extra": 1}}).json()
    assert bad["ok"] is False and len(bad["errors"]) >= 2
    assert all(set(e) == {"loc", "msg"} and e["msg"] for e in bad["errors"])
    assert any(e["loc"].startswith("sections.1") for e in bad["errors"])
    formula = api.post("/api/check", json={"kind": "metric", "spec": {"query": {
        **QUERY, "series": [{"id": "a", "label": "A", "formula": "__import__('os')"}]}}}).json()
    assert formula["ok"] is False and formula["errors"]
    assert api.get("/api/definitions").json() == []


def test_check_hints_at_a_grouped_table_without_instance(api):
    matrix = {"id": "m", "type": "matrix", "title": "Fächer", "rows": {
        "property": "ccm:taxonid", "sections": [{"id": "g", "label": "G", "values": ["x"]}]},
        "series": [{"id": "gesamt", "label": "Gesamt"}],
        "blocks": [{"id": "b", "label": "Bestand", "source": "query"}]}
    spec = {"title": "Bericht", "sections": [matrix]}
    out = api.post("/api/check", json={"kind": "template", "spec": spec}).json()
    assert out["ok"] is True and len(out["hints"]) == 1
    assert "Fächer" in out["hints"][0]["msg"] and "Instanz" in out["hints"][0]["msg"]
    assert out["hints"][0]["loc"] == "sections.0"
    for fixed in ({**spec, "instance": "wlo-prod"},
                  {**spec, "sections": [{**matrix, "instance": "wlo-prod"}]},
                  {**spec, "sections": [{**matrix, "blocks": [
                      {"id": "h", "label": "Hand", "source": "manual"}]}]}):
        assert api.post("/api/check", json={"kind": "template", "spec": fixed}).json() == {
            "ok": True, "errors": [], "hints": []}
    broken = api.post("/api/check", json={"kind": "template", "spec": {"sections": 3}}).json()
    assert broken["ok"] is False and broken["hints"] == []


def test_settings_name_the_export_formats(api):
    export = api.get("/api/settings").json()["export"]
    assert export["formats"] == ["json", "csv", "md", "html", "xlsx", "pdf"]
    assert export["pdf_available"] is pdf_available()


def test_unknown_instance_has_one_name(api):
    m = make(api, "metric", "M", {"query": QUERY})
    a = api.post("/api/query", json={"instance": "zzz"})
    b = api.post(f"/api/definitions/{m['id']}/run", json={"instance": "zzz"})
    assert a.json()["kind"] == b.json()["kind"] == "unknown_instance"
