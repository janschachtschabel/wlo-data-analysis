import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from wda.api.builtin import bundles
from wda.main import create_app
from wda.store.specs import parse_spec

from .conftest import ADMIN_PASSWORD
from .esdata import DataRepo
from .test_oersi import Oersi

APPENDIX_A = """Ethik, Geschichte, Philosophie, Religion, Interkulturelle Bildung,
Alt-Griechisch, Chinesisch, Englisch, Esperanto, Französisch, Deutsch,
Deutsch als Zweitsprache, Italienisch, Latein, Niederdeutsch, Russisch, Sorbisch, Spanisch,
Türkisch, Sport, Wirtschaftskunde, Wirtschaft und Verwaltung, Pädagogik, Mediendidaktik,
Medienbildung, Politik, Psychologie, Sexualerziehung, Sozialpädagogik, Gesellschaftskunde,
Sonderpädagogik, Verkehrserziehung, Arbeitssicherheit, Arbeitslehre, Astronomie,
Sachunterricht, Biologie, Chemie, Geografie, Mathematik, Physik, MINT, Gesundheit,
Ernährung und Hauswirtschaft, Agrarwirtschaft, Farbtechnik und Raumgestaltung, Informatik,
Elektrotechnik, Werken, Hauswirtschaft, Metalltechnik, Bautechnik,
Textiltechnik und Bekleidung, Holztechnik, Berufliche Bildung, Kunst, Darstellendes Spiel,
Musik, Allgemein, Nachhaltigkeit, Open Educational Resources, Zeitgemäße Bildung,
Weiterbildung, Sonstiges"""
DISC = "http://w3id.org/openeduhub/vocabs/discipline/"


def spec():
    return bundles()["oer-statistik"]["definitions"][0]["spec"]


def matrix():
    return next(s for s in spec()["sections"] if s["type"] == "matrix")


def test_bundled_template_is_valid_and_complete():
    parsed = parse_spec("template", spec())
    assert parsed.title == "OER-Statistik"
    sections = matrix()["rows"]["sections"]
    assert len(sections) == 10
    values = [v["value"] for s in sections for v in s["values"]]
    extra = [v["value"] for s in sections for v in s["extra"]]
    # jedes Schulfach und jeder Knoten der Destatis-Systematik in genau einer Fächergruppe
    assert len(values) == len(set(values)) == 70
    assert len(extra) == len(set(extra)) == 344
    assert all(v.startswith(DISC) for v in values)
    assert all("/hochschulfaechersystematik/n" in v for v in extra)
    assert len(APPENDIX_A.split(",")) == 64 and len(values) >= 64
    assert [b["source"] for b in matrix()["blocks"]] == ["query"] * 4 + ["oersi"] + [
        "manual"] * 2
    assert matrix()["show"] == ["gesamt", "a", "b", "c", "d", "oer_rund"]


def test_bundle_file_matches_what_the_package_ships():
    path = Path(__file__).resolve().parent.parent / "wda" / "builtin" / "oer-statistik.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["format"] == "wda-definitions" and data["builtin"]["id"] == "oer-statistik"
    pyproject = (path.parent.parent.parent / "pyproject.toml").read_text()
    assert "builtin/*.json" in pyproject  # sonst fehlt die Datei im Image


@pytest.fixture
def api(settings):
    repo, oersi = DataRepo(), Oersi()

    import httpx

    def handler(request):
        return (oersi if request.url.host == "oersi.org" else repo).handler(request)

    with TestClient(create_app(settings, transport=httpx.MockTransport(handler))) as client:
        assert client.post("/api/auth/login",
                           json={"password": ADMIN_PASSWORD}).status_code == 204
        client.repo, client.oersi = repo, oersi
        yield client


def test_list_install_run_and_export(api):
    listed = api.get("/api/builtin").json()
    assert [b["id"] for b in listed] == ["oer-statistik"]
    assert listed[0]["definitions"] == [{"id": "builtin-oer-statistik", "kind": "template",
                                         "name": "OER-Statistik", "installed": False}]
    r = api.post("/api/builtin/oer-statistik/install")
    assert r.status_code == 200 and r.json()["definitions"][0]["action"] == "created"
    assert api.get("/api/builtin").json()[0]["definitions"][0]["installed"] is True
    # eigene Änderungen überleben ein erneutes Installieren
    api.put("/api/definitions/builtin-oer-statistik", json={"version": 1, "name": "Meine"})
    again = api.post("/api/builtin/oer-statistik/install").json()
    assert again["definitions"][0]["action"] == "skipped"
    assert api.get("/api/definitions/builtin-oer-statistik").json()["name"] == "Meine"
    assert api.post("/api/builtin/gibt-es-nicht/install").status_code == 404

    run = api.post("/api/definitions/builtin-oer-statistik/run").json()
    failed = [(r["key"], r["error"]) for r in run["results"] if r["error"]]
    assert run["status"] == "ok", failed
    assert len(run["results"]) == 4 * 2 + 1 + 1
    report = json.loads(api.get(f"/api/runs/{run['id']}/export?format=json").content)
    types = [b["type"] for b in report["blocks"]]
    assert types == ["text", "table", "table", "text"]
    table = report["blocks"][1]
    assert [g[0] for g in table["header_groups"]] == [
        "WLO: Elementarbereich", "WLO: Schule", "WLO: Beruf und Erwachsene",
        "WLO: Hochschule", "OERSI (direkt)", "Mundo (Handeintrag)",
        "Kita.bayern (Handeintrag)"]
    assert len(table["columns"]) == 7 * 6
    rows = {r["key"]: r for r in table["rows"]}
    total = rows["__total__"]["values"]
    docs = api.repo.docs
    ec = "http://w3id.org/openeduhub/vocabs/educationalContext/"
    elementar = [d for d in docs if ec + "elementarbereich" in d["ccm:educationalcontext"]]
    gesamt = len(elementar)
    a = sum(1 for d in elementar
            if set(d["ccm:commonlicense_key"]) & {"CC_0", "PDM", "CC_BY", "CC_BY_SA"})
    b = sum(1 for d in elementar
            if set(d["ccm:commonlicense_key"]) & {"CC_BY_NC", "CC_BY_NC_SA", "CC_BY_ND"})
    mit = sum(1 for d in elementar if d["ccm:commonlicense_key"])
    assert total[:6] == [gesamt, a, b, mit - a - b, gesamt - mit, round(a, -2)]
    # A + B + C + D = Gesamt in jeder Zeile jedes WLO-Blocks
    for row in table["rows"]:
        for start in range(0, 24, 6):
            v = row["values"][start:start + 6]
            if None not in v[:5]:
                assert v[1] + v[2] + v[3] + v[4] == v[0], (row["label"], start)
    math = rows[DISC + "380"]
    assert math["label"] == "Mathematik" and math["level"] == 1
    assert rows[DISC + "20041"]["label"] == "Chinesisch"  # Name aus dem Template
    assert "Instanz wlo-prod." in report["blocks"][0]["markdown"]
    assert rows["mathematik_naturwissenschaften"]["values"][0] >= math["values"][0]
    assert total[24:30] == [900, 650, 200, 30, 20, 700]  # OERSI
    assert total[30:] == [None] * 12  # Handeinträge noch leer
    assert "400" not in report["blocks"][0]["markdown"] and "UTC" in report["blocks"][0][
        "markdown"]
    for fmt in ("csv", "md", "html", "pdf"):
        assert api.get(f"/api/runs/{run['id']}/export?format={fmt}").status_code == 200
