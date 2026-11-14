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
    once = [c for c in codes if c != "values_ungrouped"]
    assert len(once) == len(set(once))  # je Hinweis einmal, nicht je Block


def test_values_outside_all_groups_are_named_per_block(world):
    # sonst deckten Gruppen plus „ohne Wert" den Bestand unbemerkt nicht ab
    section = matrix()
    section["rows"]["sections"] = section["rows"]["sections"][:1]
    section["blocks"] = section["blocks"][:2]
    [table] = build_report(run_template(world, template(section))).blocks
    loose = [n["text"] for n in table.notes if n["code"] == "values_ungrouped"]
    assert len(loose) == 2 and loose[0].startswith("Sekundarstufe I: ")
    assert loose[1].startswith("Hochschule: ") and "stehen in keiner Gruppe" in loose[1]


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


HFS = "http://w3id.org/openeduhub/vocabs/hochschulfaechersystematik/"


def oersi_matrix():
    section = matrix()
    section["rows"]["sections"][0]["extra"] = [
        "Informatik", {"value": HFS + "n4", "literal": True},
        {"value": HFS + "n42", "literal": True}]
    section["series"] = SERIES + [{"id": "rund", "label": "OER gerundet",
                                   "formula": "round(a, -2)"}]
    section["blocks"] = [section["blocks"][0], {
        "id": "oersi", "label": "OERSI", "source": "oersi",
        "counts": {"gesamt": "total", "mit": "licensed",
                   "a": ["CC_0", "PDM", "CC_BY", "CC_BY_SA"]}}]
    return section


def test_oersi_block_fills_totals_and_subject_groups(world):
    run = run_template(world, template(oersi_matrix()))
    assert run["status"] == "ok"
    assert [r["key"] for r in run["results"]] == ["oer/sek1/rows", "oer/sek1/groups",
                                                  "oer/oersi/oersi"]
    body = world.oersi.bodies[0]
    assert body["size"] == 0
    assert list(body["aggs"]["sections"]["filters"]["filters"]) == ["mint"]  # geist: kein n…
    [table] = build_report(run).blocks
    # gesamt, a, mit, d = gesamt - mit, rund = round(a, -2)
    assert row(table, "__total__").values[5:] == [900, 650, 880, 20, 700]
    assert row(table, "mint").values[5:] == [100, 60, 60, 40, 100]
    assert row(table, "geist").values[5:] == [None] * 5
    assert row(table, M).values[5:] == [None] * 5
    assert row(table, "__missing__").values[5:] == [70, 10, 10, 60, 0]
    assert table.sources[1] == {"label": "OERSI", "kind": "external", "service": "OERSI",
                                "fetched_at": table.sources[1]["fetched_at"], "queries": 1}
    # die Quelle nennt alle Abfragen des Blocks, Fachzeilen und Zwischensummen
    parts = {r["key"]: r["result"]["provenance"]["queries"] for r in run["results"][:2]}
    assert table.sources[0]["queries"] == sum(parts.values()) > parts["oer/sek1/rows"]
    assert build_report(world.go(world.runner.get_run(run["id"]))) == build_report(run)


def test_oersi_failure_leaves_the_repository_block(world):
    import httpx
    world.oersi.script = [httpx.Response(503, text="x")]
    run = run_template(world, template(oersi_matrix()))
    assert run["status"] == "partial"
    assert run["results"][2]["error"]["kind"] == "unavailable"
    [table] = build_report(run).blocks
    assert row(table, "__total__").values[0] is not None
    assert row(table, "__total__").values[5:] == [None] * 5
    assert any(n["text"].startswith("OERSI: ") for n in table.notes)


def test_show_hides_helper_columns_but_placeholders_still_work(world):
    full = build_report(run_template(world, template(matrix()))).blocks[0]
    run = run_template(world, template(matrix(show=["d", "gesamt"])))
    [table] = build_report(run).blocks
    assert [c.id for c in table.columns] == ["sek1.d", "sek1.gesamt", "hs.d", "hs.gesamt",
                                             "mundo.d", "mundo.gesamt"]
    assert table.header_groups == [("Sekundarstufe I", 2), ("Hochschule", 2), ("Mundo", 2)]
    for key in (M, "mint", "__total__", "__missing__"):
        v = row(full, key).values
        assert row(table, key).values == [v[3], v[0], v[7], v[4], v[11], v[8]]
    for bad in (["gibtesnicht"], ["a", "a"], []):
        with pytest.raises(InvalidDefinition):
            parse_spec("template", template(matrix(show=bad)))


def test_too_many_listed_rows_are_refused_when_saving():
    # die Engine nimmt höchstens 2000 Zeilenwerte; sonst scheiterte erst der Lauf
    section = matrix()
    section["rows"]["sections"] = [
        {"id": f"g{n}", "label": f"G{n}",
         "values": [{"value": f"v{n}-{i}", "literal": True} for i in range(300)]}
        for n in range(8)]
    with pytest.raises(InvalidDefinition, match="2000"):
        parse_spec("template", template(section))


def test_oersi_counts_must_name_real_columns():
    section = matrix()
    section["blocks"].append({"id": "oersi", "label": "OERSI", "source": "oersi",
                              "counts": {"gibtsnicht": "total"}})
    with pytest.raises(InvalidDefinition, match="gibtsnicht"):
        parse_spec("template", template(section))


def test_manual_blocks_alone_still_show_their_rows(world):
    section = matrix()
    section["rows"]["sections"] = [
        {"id": "mint", "label": "MINT", "values": [{"value": M, "literal": True}, "Physik"]}]
    section["rows"]["labels"] = {M: "Mathematik"}
    section["blocks"] = [{
        "id": "mundo", "label": "Mundo", "source": "manual",
        "totals": {"gesamt": 1000, "mit": 900},
        "cells": {M: {"gesamt": 200, "a": 50}, "Mathematik": {"gesamt": 1}}}]
    run = run_template(world, template(section))
    [table] = build_report(run).blocks
    assert row(table, M).label == "Mathematik" and row(table, M).values == [200, 50, None, None]
    # Formeln gelten auch für Handeinträge: D = gesamt - mit
    assert row(table, "__total__").values == [1000, None, 900, 100]
    # ein Schlüssel, der zu keiner Zeile gehört, fällt nicht still weg
    [lost] = [n["text"] for n in table.notes if n["code"] == "manual_unmatched"]
    assert "Mundo" in lost and "'Mathematik'" in lost


def test_manual_cells_accept_the_name_written_in_the_definition(world):
    # Zeilen stehen als Namen in der Definition; der Handeintrag darf denselben Namen
    # oder den aufgelösten Wert nennen, unabhängig davon, ob ein Abfrage-Block gelingt
    section = matrix()
    section["blocks"][2]["cells"] = {M: {"gesamt": 200}, "Physik": {"gesamt": 9}}
    [table] = build_report(run_template(world, template(section))).blocks
    assert row(table, M).values[8] == 200 and row(table, P).values[8] == 9
    assert not [n for n in table.notes if n["code"] == "manual_unmatched"]


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), 10 ** 15, -1e300])
def test_manual_numbers_must_be_plain_counts(bad):
    section = matrix()
    section["blocks"][2]["totals"] = {"gesamt": bad}
    with pytest.raises(InvalidDefinition):
        parse_spec("template", template(section))


@pytest.mark.parametrize("series", [
    [{"id": "max", "label": "X"}],
    [{"id": "a", "label": "A"}, {"id": "z", "label": "Z", "formula": "gibtsnicht + 1"}],
])
def test_matrix_columns_are_checked_like_a_query_when_saving(series):
    with pytest.raises(InvalidDefinition):
        parse_spec("template", template(matrix(series=series)))
