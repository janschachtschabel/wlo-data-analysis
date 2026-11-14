"""Themen je Zelle (Wissenslandkarte): Schlagwörter je Fach und Bildungsstufe."""
import json

import pytest
from fastapi.testclient import TestClient

from tests.esdata import LEVELS, SUBJECTS, DataRepo, make_docs
from wda.config import Settings
from wda.engine.topics import norm
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
                      "#Geometrie", "bpb"]
            if n % 3 == 0:
                words.append("  Bruchrechnung ")  # Rand und Dublette
        if GERMAN in doc[TAX]:
            words += ["Märchen", "Deutsch", "Grammatik"]
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
    assert first["count"] >= in_math and first["share"] >= 100  # Dubletten zählen doppelt
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
