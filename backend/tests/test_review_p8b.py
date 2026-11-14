"""Befunde aus dem Review von Paket 8b, jeweils als Test des richtigen Verhaltens."""

import random

from wda.report import heat
from wda.report.formats import _table_html
from wda.report.model import Column, Row, Table

from . import esdata
from .esdata import EC, DataRepo
from .test_engine import LVL, SRC, cells, query

CHK, CUR = "ccm:editorial_checklist", "ccm:curriculum"


def test_k1_missing_and_other_trust_an_inexact_criterion(tmp_path):
    repo = DataRepo()  # der Testbestand bildet das live gemessene Verhalten des Feldes nach
    r = query(repo, tmp_path, rows={"property": CHK, "top": 1, "other": True, "missing": True})
    got = cells(r)
    assert got["__missing__"] in (None, repo.brute(lambda d: not d[CHK]))  # 96 statt 364
    assert got["__other__"] in (None, repo.brute(repo.has(CHK, "2")))  # 304 statt 17


def test_k1b_blank_value_counts_as_value_in_missing_row(tmp_path):
    repo = DataRepo()
    for n, doc in enumerate(repo.docs):
        if n % 2 == 0:
            doc[CUR] = [""]
    fill = query(repo, tmp_path, fill={"fields": [{"property": CUR}]})
    rows = query(repo, tmp_path, rows={"property": CUR, "missing": True})
    assert fill["rows"][0]["values"][1] == 384  # Füllgrad: 384 ohne Wert
    assert cells(rows)["__missing__"] == 384  # Zeile „ohne Wert“: 184


def test_w1_relaxed_probe_accepts_overmatching_criterion(tmp_path, monkeypatch):
    orig = esdata._matches

    def loose(doc, prop, values):  # trifft auch Materialien ohne Wert im Feld
        return orig(doc, prop, values) or (prop == CUR and "political" in doc.get(LVL, []))

    monkeypatch.setattr(esdata, "_matches", loose)
    repo, rng = DataRepo(), random.Random(1)  # noqa: S311 - Testdaten
    for doc in repo.docs:
        doc[CUR] = rng.sample(["1", "10", "11", "12"], rng.choice([0, 0, 1, 2, 3]))
    r = query(repo, tmp_path, fill={"fields": [{"property": CUR}]})
    assert r["rows"][0]["values"][0] in (None, repo.brute(lambda d: bool(d[CUR])))  # 262 statt 258


def test_w2_rows_without_any_column_value_vanish(tmp_path):
    repo = DataRepo()
    for n, doc in enumerate(repo.docs):
        if n % 2 == 0:  # größte Quelle, kein Material mit Bildungsstufe (wie bpb live: 1,2 %)
            doc[SRC], doc[LVL] = ["bpb_spider"], []
    for doc in repo.docs:
        if doc[SRC] == ["bpb_spider"]:
            doc[LVL] = []
    r = query(
        repo,
        tmp_path,
        rows={"property": SRC, "top": 25},
        columns={"property": LVL, "match": EC, "top": 14},
    )
    assert "bpb_spider" in cells(r)  # fehlt ganz, ohne Hinweis


def test_w3_group_rows_are_classed_but_css_keeps_them_grey():
    table = Table(
        "k",
        "t",
        [Column("a", "A")],
        [Row("g1", "Gruppe 1", "group", [1000]), Row("g2", "Gruppe 2", "group", [10])],
        view={"type": "heatmap", "options": {}},
    )
    assert heat.classes(table) == [["h6"], ["h1"]]
    html = _table_html(table)
    assert '<tr class="group"><td class="label l0">Gruppe 1</td><td class="h6">' in html
    from wda.report.formats import _CSS as CSS  # noqa: PLC0415

    # „tr.group td“ (0,1,2) schlägt „td.h6“ (0,1,1): die Zelle bleibt grau
    assert "tr.group td { background" not in CSS


def test_w4_zero_is_marked_as_gap_where_zero_is_good():
    table = Table(
        "k",
        "t",
        [Column("count", "Anzahl"), Column("f", "Fach: ohne Wert", True)],
        [Row("a", "Quelle A", "value", [9000, 0]),
                   Row("b", "Quelle B", "value", [50, 40])],
        view={"type": "heatmap", "options": {}},
        fill={"show": "missing", "by_rows": True},
    )
    assert heat.classes(table)[0][1] != "gap"  # 0 fehlende Angaben ist keine „Lücke“


def test_heat_options_zero_plain_and_scale_by_column():
    table = Table(
        "k",
        "t",
        [Column("gesamt", "Gesamt"), Column("ohne", "ohne Lizenz", True)],
        [
            Row("a", "A", "value", [1000, 0]),
            Row("b", "B", "value", [10, 5]),
            Row("c", "C", "value", [0, -1]),
        ],
        view={"type": "heatmap", "options": {"zero": "plain"}},
    )
    assert heat.classes(table) == [["h6", ""], ["h1", "h1"], ["", ""]]
    table.view = {"type": "heatmap", "options": {"scale_by": "column"}}
    assert heat.classes(table) == [["h6", "gap"], ["h1", "h6"], ["gap", ""]]
    assert "Spalte" in heat.legend(table)


def test_values_of_the_list_without_material_are_zero_rows(tmp_path):
    from .esdata import DISC
    from .test_engine import TAX

    repo = DataRepo()
    for doc in repo.docs:
        doc[TAX] = [v for v in doc[TAX] if v != DISC + "320"]  # kein Material für Informatik
    r = query(
        repo,
        tmp_path,
        rows={"property": TAX, "match": DISC, "vocab": "inside"},
        columns={"property": LVL, "match": EC},
    )
    row = next(x for x in r["rows"] if x["key"] == DISC + "320")
    assert row["label"] == "Informatik" and set(row["values"]) == {0}
    assert any(n["code"] == "rows_unused" for n in r["provenance"]["notes"])
