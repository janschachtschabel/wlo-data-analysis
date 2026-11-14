"""Lokaler Abzug: Merkmale statt Inhalte, Auswertungen daraus."""

import asyncio
import sqlite3
import stat

import pytest
from pydantic import ValidationError

from wda.engine.engine import run_query
from wda.engine.spec import QuerySpec
from wda.errors import QueryError
from wda.instances import Instance
from wda.snapshot.features import features
from wda.snapshot.store import Snapshot

SECRET = b"geheim"
INST = Instance(id="t", name="T", base_url="https://r.example")
NOW = 1_800_000_000
DAY = 86400


def node(n, **props):
    base = {"cclom:title": [f"Titel {n}"], "ccm:replicationsource": ["a_spider"],
            "ccm:oeh_publisher_combined": ["Anbieter A"]}
    base.update(props)
    return {"ref": {"id": f"id-{n}"}, "createdAt": "2024-05-01T10:00:00Z",
            "modifiedAt": "2026-01-01T00:00:00Z", "properties": base, "title": f"Titel {n}"}


def test_features_keep_no_content():
    f = features(node(1, **{
        "cclom:title": ["  Die  Fotosynthese "], "cclom:general_description": ["Wie Pflanzen …"],
        "cclom:general_keyword": ["Biologie", "Pflanze", " "],
        "ccm:wwwurl": ["https://www.Beispiel.de/seite/?x=1#oben"],
        "ccm:taxonid": ["u1", "u2"], "ccm:commonlicense_key": ["CC_BY"],
        "ccm:author_freetext": ["Erika Muster"]}), SECRET)
    assert (f.id, f.title_len, f.desc_len, f.n_keywords) == ("id-1", 16, 14, 2)
    assert (f.n_subjects, f.has_license, f.has_author, f.n_levels) == (2, 1, 1, 0)
    assert f.url_host == "beispiel.de" and f.url_https == 1
    assert f.sources == ("a_spider",) and f.publishers == ("Anbieter A",)
    assert f.created == 1714557600
    everything = repr(f)
    for secret_text in ("Fotosynthese", "Pflanzen", "Biologie", "seite", "Erika"):
        assert secret_text not in everything


def test_same_page_and_same_title_get_the_same_key():
    def key(url):
        return features(node(1, **{"ccm:wwwurl": [url]}), SECRET).url_key
    assert key("https://www.beispiel.de/seite/") == key("http://beispiel.de/seite")
    assert key("https://beispiel.de/seite") != key("https://beispiel.de/andere")
    assert key("https://beispiel.de/seite") != features(
        node(1, **{"ccm:wwwurl": ["https://beispiel.de/seite"]}), b"anderes").url_key
    assert key("kein link") is None and key("ftp://x.de/a") is None
    a = features(node(1, **{"cclom:title": ["Der  Wald"]}), SECRET)
    b = features(node(2, **{"cclom:title": ["der wald"]}), SECRET)
    assert a.title_key == b.title_key and a.title_key != a.url_key


def test_features_of_broken_nodes():
    assert features("x", SECRET) is None and features({"ref": {}}, SECRET) is None
    assert features({"ref": {"id": "a b"}}, SECRET) is None
    f = features({"ref": {"id": "n1"}, "properties": {"cclom:title": None, "ccm:wwwurl": 7,
                                                      "cm:created": ["1714557600000"]}}, SECRET)
    assert (f.title_len, f.url_key, f.created, f.modified, f.sources) == (0, None, 1714557600,
                                                                          None, ())
    assert features(node(1, **{"cclom:general_description": ["Titel 1"]}), SECRET).title_is_desc


@pytest.fixture
def snap(tmp_path):
    snap = Snapshot(tmp_path / "abzug-t.sqlite3")
    nodes = [
        node(1, **{"cclom:general_description": ["x" * 300], "ccm:taxonid": ["u"],
                   "ccm:educationalcontext": ["s"], "cclom:general_keyword": ["a", "b", "c"],
                   "ccm:wwwurl": ["https://a.de/1"]}),
        node(2, **{"ccm:wwwurl": ["http://a.de/1/"]}),                       # dünn, Dublette
        node(3, **{"ccm:replicationsource": ["b_spider"], "ccm:wwwurl": ["https://www.a.de/1"],
                   "ccm:oeh_publisher_combined": ["Anbieter B", "Anbieter A"],
                   "cclom:general_description": ["kurz"]}),
        node(4, **{"ccm:replicationsource": ["b_spider"], "cclom:title": ["Titel 1"],
                   "ccm:oeh_publisher_combined": ["Anbieter B"], "ccm:taxonid": ["u"]}),
        node(5, **{"cclom:title": [], "ccm:replicationsource": [],
                   "ccm:oeh_publisher_combined": []}),
    ]
    nodes[3]["modifiedAt"] = "2019-01-01T00:00:00Z"
    nodes[4]["createdAt"] = None
    snap.put([features(n, SECRET) for n in nodes], 1)
    snap.set_state(finished_at=NOW, status="ok")
    yield snap
    snap.close()


def ask(snap, **spec):
    async def go():
        return await run_query(QuerySpec(instance="t", snapshot=spec), _Client(), None, snap)
    return asyncio.run(go())


class _Client:
    instance = INST


def col(result, row, column):
    ids = [c["id"] for c in result["columns"]]
    line = next(r for r in result["rows"] if r["key"] == row)
    return line["values"][ids.index(column)]


def test_text_fill_overall_and_per_source(snap):
    whole = ask(snap, analysis="freitext")
    assert whole["base_total"] == 5 and whole["total_row"] is False
    assert col(whole, "titel", "count") == 4 and col(whole, "titel", "share") == 80.0
    assert col(whole, "beschreibung", "count") == 2 and col(whole, "adresse", "count") == 3
    assert whole["columns"][1]["label"] == "Anteil %"  # Füllgrad: Ampel
    assert whole["provenance"]["queries"] == 0 and "snapshot" in {
        n["code"] for n in whole["provenance"]["notes"]}
    by = ask(snap, analysis="freitext", by="source")
    assert [r["key"] for r in by["rows"]] == ["a_spider", "b_spider"]
    assert col(by, "a_spider", "count") == 2 and col(by, "a_spider", "beschreibung") == 50.0
    assert col(by, "b_spider", "schlagwoerter") == 0
    assert [c["id"] for c in ask(snap, analysis="freitext", show="count")["columns"]] == ["count"]
    counts = ask(snap, analysis="freitext", by="source", show="count")
    assert col(counts, "b_spider", "beschreibung") == 1
    assert not any(c["derived"] for c in counts["columns"])


def test_thin_metadata_duplicates_and_age(snap):
    thin = ask(snap, analysis="duenn")
    assert col(thin, "duenn", "count") == 2            # 2 und 5
    assert col(thin, "ohne_fach", "count") == 3
    dup = ask(snap, analysis="dubletten", by="source", show="count")
    assert col(dup, "a_spider", "adresse_mehrfach") == 2
    assert col(dup, "b_spider", "adresse_mehrfach") == 1
    assert col(dup, "b_spider", "adresse_fremd") == 1  # dieselbe Seite auch bei a_spider
    assert col(dup, "a_spider", "titel_mehrfach") == 1 and col(dup, "b_spider",
                                                                "titel_mehrfach") == 1
    pub = ask(snap, analysis="dubletten", by="publisher", show="count")
    assert col(pub, "Anbieter A", "count") == 3 and col(pub, "Anbieter B", "count") == 2
    age = ask(snap, analysis="alter")
    assert col(age, "alt", "count") == 1 and col(age, "unbekannt", "count") == 0
    assert sum(r["values"][0] for r in age["rows"]) == 5
    years = ask(snap, analysis="jahrgang")
    assert {r["key"]: r["values"][0] for r in years["rows"]} == {"2024": 4, "__missing__": 1}
    hosts = ask(snap, analysis="rechner")
    assert hosts["rows"][0]["key"] == "a.de" and hosts["rows"][0]["values"][0] == 3
    assert hosts["rows"][0]["values"][2] == 2
    full = ask(snap, analysis="vollstaendigkeit")
    assert sum(r["values"][0] for r in full["rows"]) == 5


def test_every_analysis_runs_and_classes_cover_everything(snap):
    from wda.snapshot.analyses import NAMES
    for name in NAMES:
        result = ask(snap, analysis=name)
        assert result["rows"], name
    for name in ("beschreibung", "schlagwoerter", "vollstaendigkeit", "alter", "adressen"):
        assert sum(r["values"][0] for r in ask(snap, analysis=name)["rows"]) == 5, name
    assert sum(r["values"][0] for r in ask(snap, analysis="titel")["rows"][:5]) == 5
    top = ask(snap, analysis="duenn", by="source", top=1)
    assert len(top["rows"]) == 1 and "rows_top" in {n["code"] for n in
                                                    top["provenance"]["notes"]}


def test_no_snapshot_no_numbers_and_spec_rules(tmp_path):
    empty = Snapshot(tmp_path / "leer.sqlite3")
    with pytest.raises(QueryError, match="Abzug"):
        ask(empty, analysis="freitext")
    with pytest.raises(QueryError, match="Abzug"):
        asyncio.run(run_query(QuerySpec(instance="t", snapshot={"analysis": "titel"}),
                              _Client(), None))
    for bad in ({"snapshot": {"analysis": "gibtsnicht"}},
                {"snapshot": {"analysis": "jahrgang", "by": "source"}},
                {"snapshot": {"analysis": "titel", "by": "node"}},
                {"snapshot": {"analysis": "titel"}, "rows": {"property": "ccm:taxonid"}},
                {"snapshot": {"analysis": "titel"},
                 "filters": [{"property": "ccm:taxonid", "values": ["x"]}]}):
        with pytest.raises(ValidationError):
            QuerySpec(instance="t", **bad)


def test_snapshot_file_is_private_and_holds_no_text(snap, tmp_path):
    mode = stat.S_IMODE(snap.path.stat().st_mode)
    assert mode & 0o077 == 0
    snap.close()  # schreibt alles in die eine Datei
    raw = b"".join(p.read_bytes() for p in snap.path.parent.glob(snap.path.name + "*"))
    assert b"Titel 1" not in raw and b"a.de/1" not in raw
    con = sqlite3.connect(snap.path)
    names = {r[1] for r in con.execute("PRAGMA table_info(nodes)")}
    assert not names & {"title", "description", "url", "keywords"}


def test_full_pass_drops_what_is_gone(snap):
    snap.put([features(node(1), SECRET), features(node(9), SECRET)], 2)
    assert snap.finish_pass(2) == 4 and snap.count() == 2
    left = snap.run(lambda con: {r[0] for r in con.execute("SELECT node FROM memberships")})
    assert left == {"id-1", "id-9"}
