import pytest

from wda.report.build import build_report
from wda.report.model import Failure, Table
from wda.store.specs import InvalidDefinition, parse_spec

from .esdata import DISC, EC
from .test_runner import World

TAX, LVL, LIC = "ccm:taxonid", "ccm:educationalcontext", "ccm:commonlicense_key"
M, P, D, G, INF = (DISC + n for n in ("380", "460", "120", "240", "320"))
S1, HS = EC + "sekundarstufe_1", EC + "hochschule"
OER = {"CC_0", "PDM", "CC_BY", "CC_BY_SA"}
SERIES = [
    {"id": "gesamt", "label": "Gesamt"},
    {"id": "a", "label": "A: OER", "filters": [
        {"property": "license", "values": ["OPEN", "CC_BY_OPEN"]}]},
    {"id": "mit", "label": "mit Lizenz", "filters": [{"property": LIC, "values": [""]}]},
    {"id": "d", "label": "D: ohne Lizenz", "formula": "gesamt - mit"},
]


def matrix(**changes):
    section = {
        "id": "oer", "type": "matrix", "title": "OER nach Fach",
        "rows": {"property": TAX, "sections": [
            {"id": "mint", "label": "MINT", "values": ["Mathematik", "Physik"],
             "extra": ["Informatik"]},
            {"id": "geist", "label": "Geisteswissenschaften", "values": ["Deutsch", G]},
        ]},
        "series": SERIES,
        "blocks": [
            {"id": "sek1", "label": "Sekundarstufe I", "source": "query", "filters": [
                {"property": LVL, "values": ["Sekundarstufe I"]}]},
            {"id": "hs", "label": "Hochschule", "source": "query", "filters": [
                {"property": LVL, "values": ["Hochschule"]}]},
            {"id": "mundo", "label": "Mundo", "source": "manual", "as_of": "2025-10-01",
             "note": "aus der Vorlage", "totals": {"gesamt": 1000, "a": 400},
             "sections": {"mint": {"gesamt": 300}}, "cells": {M: {"gesamt": 200, "a": 50}}},
        ]}
    section.update(changes)
    return section


def template(*sections, **extra):
    return {"title": "OER-Statistik", "instance": "a", "sections": list(sections), **extra}


@pytest.fixture
def world(tmp_path):
    w = World(tmp_path)
    yield w
    w.services.db.close()


def run_template(world, spec, **kwargs):
    t = world.go(world.store.create("template", "T", spec))
    return world.go(world.runner.run(t.id, **kwargs))


def row(table, key):
    return next(r for r in table.rows if r.key == key)


def test_matrix_numbers_match_brute_force(world):
    run = run_template(world, template(matrix()))
    assert run["status"] == "ok"
    assert [r["key"] for r in run["results"]] == [
        "oer/sek1/rows", "oer/sek1/groups", "oer/hs/rows", "oer/hs/groups"]
    [table] = build_report(run).blocks
    assert isinstance(table, Table)
    assert table.header_groups == [("Sekundarstufe I", 4), ("Hochschule", 4), ("Mundo", 4)]
    assert [c.id for c in table.columns][:5] == ["sek1.gesamt", "sek1.a", "sek1.mit", "sek1.d",
                                                 "hs.gesamt"]
    assert [c.derived for c in table.columns][:4] == [False, False, False, True]
    assert [(r.key, r.level, r.kind) for r in table.rows] == [
        ("mint", 0, "group"), (M, 1, "value"), (P, 1, "value"),
        ("geist", 0, "group"), (D, 1, "value"), (G, 1, "value"),
        ("__missing__", 0, "missing"), ("__total__", 0, "total")]
    assert row(table, M).label == "Mathematik"

    a = world.a
    docs = a.docs

    def count(level, pred):
        return sum(1 for d in docs if level in d[LVL] and pred(d))

    def cols(level, pred):
        gesamt = count(level, pred)
        oer = count(level, lambda d: pred(d) and set(d[LIC]) & OER)
        mit = count(level, lambda d: pred(d) and bool(d[LIC]))
        return [gesamt, oer, mit, gesamt - mit]

    def has(*uris):
        return lambda d: bool(set(uris) & set(d[TAX]))

    for offset, level in ((0, S1), (4, HS)):
        assert row(table, M).values[offset:offset + 4] == cols(level, has(M))
        assert row(table, G).values[offset:offset + 4] == cols(level, has(G))
        # Zwischensumme: jedes Material einmal, mit dem Zusatzwert Informatik
        assert row(table, "mint").values[offset:offset + 4] == cols(level, has(M, P, INF))
        assert row(table, "geist").values[offset:offset + 4] == cols(level, has(D, G))
        assert row(table, "__missing__").values[offset:offset + 4] == cols(
            level, lambda d: not d[TAX])
        assert row(table, "__total__").values[offset:offset + 4] == cols(level, lambda d: True)
        # Review-Focus 3
        sub, total = row(table, "mint").values[offset], row(table, "__total__").values[offset]
        assert max(row(table, M).values[offset], row(table, P).values[offset]) <= sub <= total
        assert sub < row(table, M).values[offset] + row(table, P).values[offset] + count(
            level, has(INF))  # keine Summe
    # Handeintrag: was fehlt, bleibt leer
    assert row(table, "__total__").values[8:] == [1000, 400, None, None]
    assert row(table, "mint").values[8:] == [300, None, None, None]
    assert row(table, M).values[8:] == [200, 50, None, None]
    assert row(table, P).values[8:] == [None] * 4
    kinds = [s["kind"] for s in table.sources]
    assert kinds == ["query", "query", "manual"] and table.sources[2]["as_of"] == "2025-10-01"
    assert table.sources[0]["filters"] == f"{LVL} = Sekundarstufe I"
    codes = [n["code"] for n in table.notes]
    assert "matrix_counting" in codes and "prefix_empty" in codes
    assert len(codes) == len(set(codes))  # je Hinweis einmal, nicht je Block


def test_placeholders_reach_into_the_matrix(world):
    run = run_template(world, template(
        {"id": "text", "type": "text", "markdown": (
            "Sek I: {{oer.total.sek1.a}} OER von {{oer.total.sek1.gesamt}}, Mathematik "
            f"{{{{oer.cell.{M}.hs.gesamt}}}}, MINT {{{{oer.cell.mint.sek1.gesamt}}}}.")},
        matrix()))
    report = build_report(run)
    table = report.blocks[1]
    total = row(table, "__total__").values
    assert report.blocks[0].markdown == (
        f"Sek I: {total[1]} OER von {total[0]}, Mathematik {row(table, M).values[4]}, "
        f"MINT {row(table, 'mint').values[0]}.")
    assert report.notes == []


def test_failed_block_leaves_the_others_standing(world):
    section = matrix()
    section["blocks"][1]["filters"] = [{"property": LVL, "values": ["Hochschle"]}]
    run = run_template(world, template(section))
    assert run["status"] == "partial"
    [table] = build_report(run).blocks
    assert isinstance(table, Table)
    assert row(table, M).values[4:8] == [None] * 4 and row(table, M).values[0] is not None
    assert row(table, "__total__").values[4:8] == [None] * 4
    failed = [n for n in table.notes if n["code"] == "block_failed"]
    assert len(failed) == 1 and failed[0]["text"].startswith("Hochschule: ")
    assert [s["kind"] for s in table.sources] == ["query", "failed", "manual"]
    # alle Abfrage-Blöcke kaputt und kein Handeintrag: Fehler statt leerer Tabelle
    section = matrix()
    section["blocks"] = [{"id": "x", "label": "X", "source": "query", "filters": [
        {"property": LVL, "values": ["Hochschle"]}]}]
    run = run_template(world, template(section))
    [block] = build_report(run).blocks
    assert isinstance(block, Failure) and run["status"] == "failed"


def test_instance_comes_from_template_run_or_section(world):
    spec = template(matrix())
    del spec["instance"]
    run = run_template(world, spec)
    assert run["status"] == "failed"
    assert "keine Instanz" in run["results"][0]["error"]["detail"]
    t = world.go(world.store.list(kind="template"))[0]
    ok = world.go(world.runner.run(t["id"], instance="b"))
    assert ok["status"] == "ok"
    assert ok["results"][0]["result"]["provenance"]["instance"]["id"] == "b"
    table = build_report(ok).blocks[0]
    assert row(table, "__total__").values[0] == world.b.brute(world.b.has(LVL, S1))


def test_stored_filters_and_subtotal_none(world):
    f = world.go(world.store.create("filter", "Nur OERSI", {"filters": [
        {"property": "ccm:replicationsource", "values": ["oersi_spider"]}]}))
    run = run_template(world, template(
        matrix(rows={"property": TAX, "subtotal": "none", "missing": False, "sections": [
            {"id": "mint", "label": "MINT", "values": ["Mathematik"]}]}),
        filter_ids=[f.id]))
    assert [r["key"] for r in run["results"]] == ["oer/sek1/rows", "oer/hs/rows"]
    [table] = build_report(run).blocks
    assert [(r.key, r.kind) for r in table.rows] == [
        ("mint", "group"), (M, "value"), ("__total__", "total")]
    assert row(table, "mint").values[:4] == [None] * 4  # keine Zwischensumme verlangt
    assert row(table, M).values[0] == sum(
        1 for d in world.a.docs if S1 in d[LVL] and M in d[TAX]
        and "oersi_spider" in d["ccm:replicationsource"])


@pytest.mark.parametrize(("change", "match"), [
    ({"blocks": []}, "blocks"),
    ({"series": []}, "series"),
    ({"series": [{"id": "a", "label": "A", "formula": "a.b"}]}, "Spalte 'A'"),
    ({"rows": {"property": TAX, "sections": [{"id": "x", "label": "X"}]}}, "mindestens einen"),
    ({"rows": {"property": TAX, "sections": [
        {"id": "x", "label": "X", "values": ["a"]}, {"id": "x", "label": "Y", "values": ["b"]}]}},
     "eindeutig"),
    ({"blocks": [{"id": "x", "label": "X", "source": "excel"}]}, "blocks.0"),
    ({"blocks": [{"id": "x", "label": "X", "source": "manual", "totals": {"a": "viele"}}]},
     "totals"),
])
def test_invalid_matrix(change, match):
    with pytest.raises(InvalidDefinition, match=match):
        parse_spec("template", template(matrix(**change)))
