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
    assert [b["id"] for b in listed] == ["abdeckung", "datenprobleme", "datenqualitaet",
                                         "oer-statistik", "quellen", "themen",
                                         "tiefenpruefung"]
    listed = listed[3:]
    assert listed[0]["definitions"] == [{"id": "builtin-oer-statistik", "kind": "template",
                                         "name": "OER-Statistik", "installed": False,
                                         "changed": False}]
    r = api.post("/api/builtin/oer-statistik/install")
    assert r.status_code == 200 and r.json()["definitions"][0]["action"] == "created"
    assert api.get("/api/builtin").json()[3]["definitions"][0]["installed"] is True
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
    from wda.report.formats import pdf_available
    for fmt in ("csv", "md", "html") + (("pdf",) if pdf_available() else ()):
        assert api.get(f"/api/runs/{run['id']}/export?format={fmt}").status_code == 200


# ---- Dashboard „Datenqualität“ ---------------------------------------------------------

# Felder, die WLO Produktion als Facette annimmt (gemessen, docs/edu-sharing-api-erkenntnisse.md)
MEASURED = {
    "ccm:taxonid", "ccm:educationalcontext", "ccm:oeh_lrt_aggregated", "ccm:commonlicense_key",
    "cclom:general_language", "ccm:educationalintendedenduserrole", "ccm:replicationsource",
    "ccm:oeh_publisher_combined", "ccm:price", "ccm:conditionsOfAccess",
    "ccm:containsAdvertisement", "ccm:dataProtectionConformity", "ccm:fskRating",
    "ccm:oeh_quality_login", "ccm:editorial_checklist", "ccm:oeh_buffet_criteria",
    "ccm:curriculum", "ccm:oeh_widgets"}
# davon nicht als Filter zählbar: weder „ohne Wert“ noch „Übrige“ als Zeile
FACET_ONLY = {"ccm:price", "ccm:conditionsOfAccess", "ccm:containsAdvertisement",
              "ccm:dataProtectionConformity", "ccm:fskRating", "ccm:oeh_quality_login",
              "ccm:oeh_buffet_criteria", "ccm:editorial_checklist"}


def quality():
    return bundles()["datenqualitaet"]["definitions"][0]


def test_quality_dashboard_uses_only_measured_fields():
    entry = quality()
    parsed = parse_spec("dashboard", entry["spec"])
    assert len(parsed.tiles) >= 12
    for tile in parsed.tiles:
        query = tile.query
        assert query is not None and query.instance == "wlo-prod" and not query.filters
        used = {f.property for f in query.fill.fields} if query.fill else set()
        if query.rows:
            used.add(query.rows.property)
            if query.rows.property in FACET_ONLY:
                assert not query.rows.missing and not query.rows.other, tile.id
        assert used and used <= MEASURED, tile.id
        assert tile.layout.x + tile.layout.w <= 12
    cells = set()
    for tile in parsed.tiles:  # keine Kachel liegt auf einer anderen
        area = {(x, y) for x in range(tile.layout.x, tile.layout.x + tile.layout.w)
                for y in range(tile.layout.y, tile.layout.y + tile.layout.h)}
        assert not area & cells, tile.id
        cells |= area


def test_quality_dashboard_installs_and_runs(api):
    r = api.post("/api/builtin/datenqualitaet/install")
    assert r.status_code == 200 and r.json()["definitions"][0]["action"] == "created"
    run = api.post("/api/definitions/builtin-datenqualitaet/run").json()
    results = {r["key"]: r for r in run["results"]}
    # der Testbestand kennt nicht jedes Feld von WLO; was er kennt, muss stimmen
    docs = api.repo.docs
    lic = results["lizenzen"]["result"]
    assert lic is not None and results["lizenzen"]["error"] is None
    rows = {r["key"]: r["values"][0] for r in lic["rows"]}
    assert rows["__missing__"] == sum(1 for d in docs if not d["ccm:commonlicense_key"])
    price = results["kosten"]["result"]
    assert {r["key"]: r["values"][0] for r in price["rows"]} == {
        v: sum(1 for d in docs if v in d["ccm:price"]) for v in ("no", "yes", "yes_for_additional")}
    assert results["buffet"]["error"] is None and results["erschliessung"]["error"] is None
    report = api.get(f"/api/runs/{run['id']}/report").json()
    assert len(report["blocks"]) == len(run["results"])
    assert api.get(f"/api/runs/{run['id']}/export?format=csv").status_code == 200


# ---- Dashboards „Abdeckung“ und „Datenprobleme“ ----------------------------------------

MEASURED_MORE = MEASURED | {"license", "ccm:oeh_quality_protection_of_minors",
                            "ccm:oeh_quality_currentness", "ccm:oeh_quality_neutralness",
                            "ccm:oeh_quality_medial", "ccm:oeh_quality_language",
                            "ccm:oeh_quality_didactics"}
# als Kriterium zählbar (Spalten, „Übrige“, Filter); die übrigen nur als Facette
AS_FILTER = MEASURED - FACET_ONLY | {"license"}
SINGLE = {"ccm:price", "ccm:conditionsOfAccess", "ccm:containsAdvertisement", "ccm:fskRating",
          "ccm:oeh_quality_login", "ccm:editorial_checklist", "ccm:commonlicense_key"}


@pytest.mark.parametrize("bundle_id", ["abdeckung", "datenprobleme"])
def test_coverage_and_problem_dashboards_fit_what_was_measured(bundle_id):
    entry = bundles()[bundle_id]["definitions"][0]
    assert entry["id"] == f"builtin-{bundle_id}"
    parsed = parse_spec("dashboard", entry["spec"])
    cells = set()
    for tile in parsed.tiles:
        q = tile.query
        assert q is not None and q.instance == "wlo-prod" and not q.filters, tile.id
        used = set()
        if q.fill:
            used |= {f.property for f in q.fill.fields}
        if q.vocab_check:
            used |= {f.property for f in q.vocab_check.fields}
        if q.rows:
            used.add(q.rows.property)
            if q.rows.other:
                assert q.rows.property in AS_FILTER, tile.id
            if q.rows.missing:  # braucht ein Kriterium oder ein einwertiges Feld
                assert q.rows.property in AS_FILTER | SINGLE, tile.id
        if q.columns:
            used.add(q.columns.property)
            assert q.columns.property in AS_FILTER, tile.id
        for series in q.series:
            for flt in series.filters:
                used.add(flt.property)
                assert flt.property in AS_FILTER, tile.id
        assert used and used <= MEASURED_MORE, (tile.id, used - MEASURED_MORE)
        assert tile.layout.x + tile.layout.w <= 12
        area = {(x, y) for x in range(tile.layout.x, tile.layout.x + tile.layout.w)
                for y in range(tile.layout.y, tile.layout.y + tile.layout.h)}
        assert not area & cells, tile.id
        cells |= area


def test_coverage_dashboard_runs_and_separates_school_and_university_subjects(api):
    from .esdata import DISC as SCHOOL
    from .esdata import HSF

    assert api.post("/api/builtin/abdeckung/install").status_code == 200
    run = api.post("/api/definitions/builtin-abdeckung/run").json()
    assert run["status"] == "ok", [(r["key"], r["error"]) for r in run["results"] if r["error"]]
    results = {r["key"]: r["result"] for r in run["results"]}
    docs = api.repo.docs
    school = results["schule-stufe"]
    assert school["rows"] and all(r["key"].startswith(SCHOOL) for r in school["rows"])
    assert all(c["id"].startswith("http") for c in school["columns"])  # ohne Schmutzwerte
    row, col = school["rows"][0], school["columns"][0]
    assert row["values"][0] == sum(
        1 for d in docs if row["key"] in d["ccm:taxonid"]
        and col["id"] in d["ccm:educationalcontext"])
    uni = results["hochschule-stufe"]
    assert uni["rows"] and all(r["key"].startswith(HSF) for r in uni["rows"])
    lic = results["schule-lizenz"]
    ids = [c["id"] for c in lic["columns"]]
    first = lic["rows"][0]
    having = [d for d in docs if first["key"] in d["ccm:taxonid"]]
    assert first["values"][ids.index("gesamt")] == len(having)
    assert first["values"][ids.index("ohne")] == sum(
        1 for d in having if not d["ccm:commonlicense_key"])
    report = api.get(f"/api/runs/{run['id']}/report").json()
    assert {b["view"]["type"] for b in report["blocks"]} == {"heatmap", "table"}
    # Lückenliste: aufsteigend nach dem Anteil am Erwarteten, Zahl stimmt mit der Matrix
    gaps = results["luecken-schule"]
    assert [c["id"] for c in gaps["columns"]] == ["count", "expected", "ratio"]
    ratios = [r["values"][2] for r in gaps["rows"]]
    # (der Testbestand ist gleichmäßig verteilt: Die Liste darf leer sein)
    assert ratios == sorted(ratios)
    assert all(r["values"][2] < 50 for r in gaps["rows"])
    assert results["luecken-schule-materialart"]["columns"][0]["id"] == "count"
    assert "anteil" in ids and results["hochschule-lizenz"]["rows"]
    assert results["schule-erschliessung"]["rows"] and results["sprachniveau-stufe"]["rows"]
    html = api.get(f"/api/runs/{run['id']}/export?format=html").text
    assert 'class="h6"' in html and "0 (Lücke)" in html


def test_problem_dashboard_runs_and_finds_the_dirty_values(api):
    assert api.post("/api/builtin/datenprobleme/install").status_code == 200
    run = api.post("/api/definitions/builtin-datenprobleme/run").json()
    failed = [(r["key"], r["error"]) for r in run["results"] if r["error"]]
    results = {r["key"]: r["result"] for r in run["results"]}
    docs = api.repo.docs
    # der Testbestand kennt nicht jedes Qualitätsfeld von WLO: Die Übersicht darf leer
    # bleiben, alles andere muss laufen
    assert all(key == "vokabular" for key, _ in failed), failed
    dirty = results["stufe-unbekannt"]
    assert {r["key"]: r["values"][0] for r in dirty["rows"]} == {
        "political": sum(1 for d in docs if "political" in d["ccm:educationalcontext"])}
    assert results["fach-unbekannt"]["rows"] == []
    core = results["kernangaben"]
    total = len(docs)
    for row in core["rows"]:
        filled = sum(1 for d in docs if any(v.strip() for v in d[row["key"]]))
        assert row["values"] == [filled, total - filled, round(100 * filled / total, 1)]
    report = api.get(f"/api/runs/{run['id']}/report").json()
    blocks = {b["key"]: b for b in report["blocks"]}
    assert blocks["kernangaben"]["view"] == {"type": "heatmap", "options": {"scale": "rate"}}
