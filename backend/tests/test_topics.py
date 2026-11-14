"""Themen je Zelle (Wissenslandkarte): Schlagwörter je Fach und Bildungsstufe."""
import json

import pytest
from fastapi.testclient import TestClient

from tests.esdata import LEVELS, SUBJECTS, DataRepo, make_docs
from wda.config import Settings
from wda.engine.topics import _blocks, _is_name, _names, _too_short, norm
from wda.main import create_app

PASSWORD = "themen-passwort"
TAX, LVL, KEY = "ccm:taxonid", "ccm:educationalcontext", "cclom:general_keyword"
MATH, GERMAN = SUBJECTS["Mathematik"], SUBJECTS["Deutsch"]
PRIMARY = LEVELS["Primarstufe"]


def docs():
    """Der übliche Bestand; Mathematik trägt Brüche, Deutsch Märchen, alle tragen Füllwörter."""
    out = make_docs()
    for n, doc in enumerate(out):
        words = ["Nachhilfe", "einfach erklärt"]
        if MATH in doc[TAX]:
            words += ["Bruchrechnung" if n % 2 else "bruchrechnung", "Mathematik", "mathe",
                      "bpb"]
            if n % 4:
                words.append("#Geometrie")
            if n == 1:
                words += ["Unikat", "#unikat", "unikat "]  # ein Material, drei Schreibweisen
            if n % 3 == 0:
                words.append("  Bruchrechnung ")  # Rand und Dublette
        if GERMAN in doc[TAX]:
            words += ["Deutsch", "Grammatik"]
            if n % 3:
                words.append("Märchen")
        if PRIMARY in doc[LVL]:
            words.append("Grundschule")
        words.append(f"einzeln-{n}")  # trägt nur ein Material: nie ein Thema
        doc[KEY] = words
    return out


@pytest.fixture
def repo():
    return DataRepo(docs())


@pytest.fixture
def api(tmp_path, repo):
    settings = Settings(admin_password=PASSWORD, cookie_secure=False, data_dir=tmp_path,
                        instances=json.dumps([]))
    with TestClient(create_app(settings, transport=repo.transport())) as client:
        assert client.post("/api/auth/login", json={"password": PASSWORD}).status_code == 204
        yield client


def ask(api, **query):
    return api.post("/api/query", json={"instance": "wlo-prod", **query})


def cell(result, row_label, column=0):
    row = next(r for r in result["rows"] if r["label"] == row_label)
    return row["values"][column], row["topics"][column]


def test_norm_joins_spellings():
    assert norm("  #Bruchrechnung\n") == norm("bruchrechnung") == "bruchrechnung"
    assert norm("Deutsch_als  Zweitsprache") == "deutsch als zweitsprache"


def test_topics_per_subject_are_cleaned_and_counted(api, repo):
    out = ask(api, rows={"property": TAX, "include": ["Mathematik", "Deutsch"]},
              topics={"property": KEY, "exclude": ["bpb"]})
    assert out.status_code == 200, out.text
    result = out.json()
    count, topics = cell(result, "Mathematik")
    in_math = repo.brute(lambda d: MATH in d[TAX])
    assert count == in_math
    labels = [t["label"] for t in topics]
    # Schreibweisen zusammengeführt, Fachname und Kurzform, Füllwörter und Sperrliste fehlen
    assert labels[0].casefold() == "bruchrechnung" and "Geometrie" in labels
    assert not {"mathematik", "mathe", "nachhilfe", "einfach erklärt", "bpb", "deutsch"} & {
        label.casefold() for label in labels}
    assert not any(label.startswith("einzeln-") for label in labels)
    first = topics[0]
    assert first["count"] >= in_math and first["share"] == 100  # Dubletten zählen doppelt
    assert "unikat" not in {label.casefold() for label in labels}
    assert [t["label"] for t in cell(result, "Deutsch")[1]][:2] == ["Grammatik", "Märchen"]
    assert result["topics"] == {"property": KEY, "rank": "typical", "free": True}
    codes = {n["code"] for n in result["provenance"]["notes"]}
    assert {"topics", "topics_cleaned"} <= codes


def test_topics_as_a_matrix_and_plain_frequency(api, repo):
    out = ask(api, rows={"property": TAX, "include": ["Mathematik"]},
              columns={"property": LVL, "include": ["Primarstufe", "Sekundarstufe I"]},
              topics={"property": KEY, "rank": "frequent", "stopwords": False, "top": 3})
    assert out.status_code == 200, out.text
    result = out.json()
    count, topics = cell(result, "Mathematik", 0)
    assert count == repo.brute(lambda d: MATH in d[TAX] and PRIMARY in d[LVL])
    assert len(topics) == 3 and all("lift" not in t for t in topics)
    # ohne Füllwort-Liste stehen die häufigen Wörter vorn; die Stufe selbst bleibt draußen
    assert {"Nachhilfe", "einfach erklärt"} & {t["label"] for t in topics}
    assert "Primarstufe" not in [t["label"] for t in topics]


def test_topics_from_a_vocabulary_keep_their_labels(api):
    out = ask(api, rows={"property": TAX, "top": 3},
              topics={"property": "ccm:curriculum", "rank": "frequent"})
    assert out.status_code == 200, out.text
    result = out.json()
    assert result["topics"]["free"] is False
    assert "topics_cleaned" not in {n["code"] for n in result["provenance"]["notes"]}


@pytest.mark.parametrize("query", [
    {"topics": {"property": KEY}},                                   # ohne Zeilen
    {"rows": {"property": TAX}, "topics": {"property": KEY}},        # Zeilen ohne top
    {"rows": {"property": TAX, "top": 3}, "topics": {"property": KEY}, "percent": "row"},
    {"rows": {"property": TAX, "top": 40}, "columns": {"property": LVL, "top": 40},
     "topics": {"property": KEY}},                                   # zu viele Zellen
    {"rows": {"property": TAX, "top": 3}, "topics": {"property": TAX}},
    {"rows": {"property": TAX, "top": 3}, "topics": {"property": "cclom:title"}},
])
def test_what_does_not_go(api, query):
    assert ask(api, **query).status_code in (400, 422)


def test_the_report_carries_topics_into_every_format(api):
    made = api.post("/api/definitions", json={"kind": "metric", "name": "Karte", "spec": {
        "query": {"instance": "wlo-prod", "rows": {"property": TAX, "include": ["Mathematik"]},
                  "columns": {"property": LVL, "include": ["Primarstufe"]},
                  "topics": {"property": KEY}},
        "view": {"type": "heatmap"}}})
    assert made.status_code == 201, made.text
    run = api.post(f"/api/definitions/{made.json()['id']}/run", json={"save": True}).json()
    assert run["status"] == "ok", run
    for fmt, needle in (("json", '"topics"'), ("csv", "Bruchrechnung"), ("md", "Bruchrechnung"),
                        ("html", "Bruchrechnung")):
        got = api.get(f"/api/runs/{run['id']}/export", params={"format": fmt})
        assert got.status_code == 200, got.text
        assert needle.casefold() in got.text.casefold(), fmt


def test_the_assistant_sees_topics_in_short(api):
    from wda.llm.tools import compact
    result = ask(api, rows={"property": TAX, "include": ["Mathematik"]},
                 topics={"property": KEY, "exclude": ["bpb"]}).json()
    short = compact(result)
    assert short["themen"][0][0] == "Mathematik"
    assert "bruchrechnung" in short["themen"][0][1].casefold()


def test_excel_export_has_a_sheet_per_table_numbers_and_topics(api):
    import io
    import zipfile
    made = api.post("/api/definitions", json={"kind": "metric", "name": "=SUM(A1) Karte", "spec": {
        "query": {"instance": "wlo-prod", "rows": {"property": TAX, "include": ["Mathematik"]},
                  "topics": {"property": KEY}}}})
    run = api.post(f"/api/definitions/{made.json()['id']}/run", json={"save": True}).json()
    got = api.get(f"/api/runs/{run['id']}/export", params={"format": "xlsx"})
    assert got.status_code == 200, got.text
    assert got.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    book = zipfile.ZipFile(io.BytesIO(got.content))
    assert book.testzip() is None
    names = book.namelist()
    assert "xl/workbook.xml" in names and "xl/worksheets/sheet2.xml" in names
    sheet = book.read("xl/worksheets/sheet2.xml").decode()
    assert "<v>" in sheet and "Themen je Zelle" in sheet and "ruchrechnung" in sheet
    # Texte sind Text, nie Formel
    assert "<f>" not in sheet and 't="inlineStr"' in sheet
    import xml.dom.minidom
    for name in names:
        if name.endswith((".xml", ".rels")):
            xml.dom.minidom.parseString(book.read(name))  # noqa: S318 - eigene Datei


def test_block_rule_spares_real_topics_and_finds_a_channels_set():
    # vier gleich große Themen in einer kleinen Zelle sind kein Satz eines Kanals
    real = [("prozentrechnung", 20), ("geometrie", 20), ("algebra", 20), ("bruchrechnung", 20)]
    assert _blocks(real, 400) == set()
    # sechs Wörter an denselben 66 Materialien, Füllwörter eingeschlossen, sind einer
    channel = [("simpleclub", 66), ("mechanik", 66), ("einfach erklärt", 66), ("lernvideo", 65),
               ("nachhilfe", 66), ("physikvideo", 66), ("optik", 30)]
    assert _blocks(channel, 400) == {key for key, _ in channel[:6]}
    # und nicht, wenn jedes nur einen kleinen Teil der Zelle trägt
    assert _blocks(channel, 4000) == set()


def test_block_rule_finds_large_sets_in_large_cells():
    # live gemessen: fünf Wörter eines Kanals zwischen 504 und 518 in einer Zelle mit 8.153
    war = [("wilhelm ii", 518), ("winston churchill", 512), ("1914 to 1918", 507),
           ("history channel", 507), ("archduke franz ferdinand", 504), ("battlefield 1", 440)]
    assert _blocks(war, 8153) == {key for key, _ in war[:5]}


def test_names_and_short_words():
    names, whole = _names("Physik"), ("physik",)
    assert _is_name("physik", names, whole)
    assert not _is_name("physikdidaktik", names, whole)
    assert not _is_name("physikalische größen", names, whole)
    assert _is_name("mathe", _names("Mathematik"), ("mathematik",))
    assert not _is_name("deutschland", _names("Deutsch"), ("deutsch",))
    assert not _is_name("bild", _names("Berufliche Bildung"), ("berufliche bildung",))
    assert _is_name("bildung", _names("Berufliche Bildung"), ("berufliche bildung",))
    assert not _too_short("ki", "KI") and not _too_short("eu", "EU")
    assert not _too_short("3d", "3d") and _too_short("zu", "zu") and _too_short("a", "A")
    assert not _too_short("ddr", "ddr")


def run_scoped(api, query, scope):
    made = api.post("/api/definitions", json={
        "kind": "metric", "name": "Karte", "spec": {"query": {"instance": "wlo-prod", **query}}})
    assert made.status_code == 201, made.text
    out = api.post(f"/api/definitions/{made.json()['id']}/run",
                   json={"save": False, "scope": scope})
    assert out.status_code == 200, out.text
    return out.json()["results"][0]


def test_a_context_on_the_row_field_picks_the_row(api):
    query = {"rows": {"property": TAX, "top": 5}, "topics": {"property": KEY}}
    out = run_scoped(api, query, [{"property": TAX, "values": ["Mathematik"]}])
    assert out.get("error") is None, out
    assert [row["label"] for row in out["result"]["rows"]] == ["Mathematik"]
    out = run_scoped(api, query, [{"property": KEY, "values": [
        {"value": "Geometrie", "literal": True}]}])
    assert out["error"]["kind"] == "not_applicable", out
