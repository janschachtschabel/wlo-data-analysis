import asyncio

import httpx
import pytest

from wda.cache import TTLCache
from wda.edusharing.client import EduSharingClient
from wda.edusharing.errors import EduSharingError
from wda.edusharing.vocab import UnresolvedValue, VocabStore
from wda.engine.engine import run_query
from wda.engine.formula import QueryError
from wda.engine.spec import QuerySpec
from wda.instances import Instance

from .esdata import DISC, EC, LICENSES, DataRepo

M, P, D = DISC + "380", DISC + "460", DISC + "120"
S1, HS, EL = EC + "sekundarstufe_1", EC + "hochschule", EC + "elementarbereich"
TAX, LVL, LIC, SRC = ("ccm:taxonid", "ccm:educationalcontext", "ccm:commonlicense_key",
                      "ccm:replicationsource")
OER = {"CC_0", "PDM", "CC_BY", "CC_BY_SA"}


@pytest.fixture
def repo():
    return DataRepo()


def query(repo, tmp_path, ttl=0, **spec):
    async def go():
        http = httpx.AsyncClient(transport=repo.transport())
        client = EduSharingClient(Instance(id="t", name="T", base_url="https://r.example"),
                                  http, TTLCache(ttl=ttl))
        vocab = VocabStore(client, tmp_path)
        return await run_query(QuerySpec(instance="t", **spec), client, vocab)

    return asyncio.run(go())


def cells(result, column=0):
    return {r["key"]: r["values"][column] for r in result["rows"]}


def test_total_only(repo, tmp_path):
    r = query(repo, tmp_path)
    assert r["rows"] == []
    assert r["columns"] == [{"id": "anzahl", "label": "Anzahl", "derived": False, "total": 400}]
    assert r["provenance"]["queries"] == 1 and r["provenance"]["cached"] is False
    assert r["provenance"]["fetched_at"] == r["provenance"]["oldest_fetched_at"]
    r = query(repo, tmp_path, filters=[{"property": TAX, "values": ["Mathematik", "Physik"]}])
    assert r["columns"][0]["total"] == repo.brute(repo.has(TAX, M, P))
    assert r["provenance"]["filters"][0]["values"][0] == {
        "value": M, "label": "Mathematik", "how": "label"}


def test_rows_as_facet_and_true_total(repo, tmp_path):
    r = query(repo, tmp_path, rows={"property": TAX})
    got = cells(r)
    for uri in (M, P, D):
        assert got[uri] == repo.brute(repo.has(TAX, uri))
    assert [x["label"] for x in r["rows"]][0] in {"Mathematik", "Physik", "Deutsch",
                                                   "Geschichte", "Informatik"}
    counts = [x["values"][0] for x in r["rows"]]
    assert counts == sorted(counts, reverse=True)
    # mehrwertig: Zeilensumme übersteigt die echte Gesamtzahl
    assert sum(counts) > r["columns"][0]["total"] == 400
    assert all(x["kind"] == "value" for x in r["rows"])
    assert r["provenance"]["queries"] == 1


def test_rows_by_series_filters(repo, tmp_path):
    r = query(repo, tmp_path,
              filters=[{"property": LVL, "values": ["Sekundarstufe I"]}],
              rows={"property": TAX},
              series=[
                  {"id": "alle", "label": "Alle"},
                  {"id": "oer", "label": "OER",
                   "filters": [{"property": "license", "values": ["OPEN", "CC_BY_OPEN"]}]},
              ])
    in_s1 = lambda d: S1 in d[LVL]  # noqa: E731
    is_oer = lambda d: bool(OER & set(d[LIC]))  # noqa: E731
    assert r["columns"][0]["total"] == repo.brute(in_s1)
    assert r["columns"][1]["total"] == repo.brute(lambda d: in_s1(d) and is_oer(d))
    assert cells(r, 0)[M] == repo.brute(lambda d: in_s1(d) and M in d[TAX])
    assert cells(r, 1)[M] == repo.brute(lambda d: in_s1(d) and is_oer(d) and M in d[TAX])
    assert r["provenance"]["queries"] == 2
    assert r["provenance"]["columns"][1]["filters"][0]["property"] == "license"


def test_columns_from_field(repo, tmp_path):
    r = query(repo, tmp_path, rows={"property": TAX}, columns={"property": LVL, "top": 3})
    assert len(r["columns"]) == 3
    assert [c["id"] for c in r["columns"]] == ["c1", "c2", "c3"]
    totals = [c["total"] for c in r["columns"]]
    assert totals == sorted(totals, reverse=True)
    label_to_uri = {"Sekundarstufe I": S1, "Hochschule": HS, "Elementarbereich": EL,
                    "Primarstufe": EC + "grundschule"}
    for pos, col in enumerate(r["columns"]):
        uri = label_to_uri[col["label"]]
        assert col["total"] == repo.brute(repo.has(LVL, uri))
        assert cells(r, pos)[M] == repo.brute(lambda d, u=uri: u in d[LVL] and M in d[TAX])
    assert any("häufigsten" in n for n in r["provenance"]["notes"])
    assert r["provenance"]["queries"] == 4
    r = query(repo, tmp_path, columns={"property": LVL, "include": ["Hochschule", "political"
                                                                    ][:1]})
    assert [c["label"] for c in r["columns"]] == ["Hochschule"]
    assert r["rows"] == [] and r["columns"][0]["total"] == repo.brute(repo.has(LVL, HS))


def test_groups_distinct_overlapping_and_unknown(repo, tmp_path):
    r = query(repo, tmp_path, rows={"property": TAX, "groups": [
        {"id": "mint", "label": "MINT", "values": ["Mathematik", "Physik", "Informatik"]},
        {"label": "Mathe und Deutsch", "values": [M, "Deutsch"]},
        {"label": "Fremd", "values": [{"value": "gibt-es-nicht", "literal": True}]},
    ]})
    got = cells(r)
    assert got["mint"] == repo.brute(repo.has(TAX, M, P, DISC + "320"))
    assert got["g2"] == repo.brute(repo.has(TAX, M, D))
    assert got["g3"] == 0
    assert [x["label"] for x in r["rows"]] == ["MINT", "Mathe und Deutsch", "Fremd"]
    assert all(x["kind"] == "group" for x in r["rows"])
    assert r["provenance"]["queries"] == 4
    # distinct ist keine Summe der Einzelwerte
    assert got["mint"] < sum(repo.brute(repo.has(TAX, u)) for u in (M, P, DISC + "320"))


def test_groups_sum_on_single_valued_license(repo, tmp_path):
    r = query(repo, tmp_path, rows={"property": LIC, "groups": [
        {"id": "a", "label": "A: OER", "values": ["CC_0", "PDM", "CC_BY", "CC_BY_SA"]},
        {"id": "b", "label": "B: NC/ND", "values": ["CC_BY_NC", "CC_BY_NC_SA", "CC_BY_ND"]},
        {"id": "c", "label": "C", "values": ["COPYRIGHT_FREE", "CUSTOM"]},
    ], "missing": True})
    got = cells(r)
    assert got["a"] == repo.brute(repo.has(LIC, *OER))
    assert got["b"] == repo.brute(repo.has(LIC, "CC_BY_NC", "CC_BY_NC_SA", "CC_BY_ND"))
    assert got["c"] == repo.brute(repo.has(LIC, "COPYRIGHT_FREE", "CUSTOM"))
    assert got["__missing__"] == repo.brute(lambda d: not d[LIC])
    assert got["a"] + got["b"] + got["c"] + got["__missing__"] == 400
    assert r["provenance"]["queries"] == 1  # alles aus einer Facette
    assert any("Summen" in n for n in r["provenance"]["notes"])
    assert r["rows"][-1] == {"key": "__missing__", "label": "ohne Wert", "kind": "missing",
                             "values": [got["__missing__"]]}


def test_group_mode_explicit_overrides_auto(repo, tmp_path):
    groups = [{"id": "mp", "label": "M+P", "values": [M, P]}]
    summed = query(repo, tmp_path, rows={"property": TAX, "groups": groups,
                                         "group_mode": "sum"})
    assert cells(summed)["mp"] == repo.brute(repo.has(TAX, M)) + repo.brute(repo.has(TAX, P))
    with pytest.raises(EduSharingError) as info:  # einwertiges Feld verträgt kein ODER
        query(repo, tmp_path, rows={"property": LIC, "group_mode": "distinct", "groups": [
            {"label": "x", "values": ["CC_0", "PDM"]}]})
    assert info.value.kind == "single_value"


def test_missing_on_multi_valued_field(repo, tmp_path):
    r = query(repo, tmp_path, rows={"property": TAX, "missing": True},
              series=[{"id": "alle", "label": "Alle"},
                      {"id": "hs", "label": "Hochschule",
                       "filters": [{"property": LVL, "values": [HS]}]}])
    assert cells(r, 0)["__missing__"] == repo.brute(lambda d: not d[TAX])
    assert cells(r, 1)["__missing__"] == repo.brute(lambda d: HS in d[LVL] and not d[TAX])
    assert r["rows"][-1]["kind"] == "missing"
    assert r["provenance"]["queries"] == 4
    # auch mit Gruppen (distinct)
    r = query(repo, tmp_path, rows={"property": TAX, "missing": True, "groups": [
        {"label": "M", "values": [M]}]})
    assert cells(r)["__missing__"] == repo.brute(lambda d: not d[TAX])


def test_missing_with_dirty_and_empty_result(repo, tmp_path):
    r = query(repo, tmp_path, rows={"property": LVL, "missing": True})
    assert cells(r)["__missing__"] == repo.brute(lambda d: not d[LVL])
    assert cells(r)["political"] == repo.brute(repo.has(LVL, "political"))
    assert next(x for x in r["rows"] if x["key"] == "political")["label"] == "political"
    empty = query(repo, tmp_path, percent="column",
                  filters=[{"property": SRC, "values": ["kein_spider"]}],
                  rows={"property": TAX, "missing": True, "top": 3, "other": True})
    assert empty["columns"][0]["total"] == 0
    assert empty["rows"] == [{"key": "__missing__", "label": "ohne Wert", "kind": "missing",
                              "values": [0], "percent": [None]}]


def test_row_field_also_in_filters(repo, tmp_path):
    r = query(repo, tmp_path, filters=[{"property": TAX, "values": ["Mathematik", "Physik"]}],
              rows={"property": TAX})
    assert set(cells(r)) == {M, P}
    assert cells(r)[M] == repo.brute(repo.has(TAX, M))
    assert r["columns"][0]["total"] == repo.brute(repo.has(TAX, M, P))
    assert any("nur die gefilterten" in n for n in r["provenance"]["notes"])
    for rows in ({"property": TAX, "missing": True},
                 {"property": TAX, "groups": [{"label": "x", "values": [M]}]}):
        with pytest.raises(QueryError):
            query(repo, tmp_path, filters=[{"property": TAX, "values": [M]}], rows=rows)


def test_include_sort_top_other(repo, tmp_path):
    r = query(repo, tmp_path, rows={"property": TAX, "include": ["Physik", "Mathematik"]})
    assert [x["label"] for x in r["rows"]] == ["Physik", "Mathematik"]
    r = query(repo, tmp_path, rows={"property": TAX, "sort": "label"})
    labels = [x["label"] for x in r["rows"]]
    assert labels == sorted(labels, key=str.casefold) and len(labels) == 5
    # mehrwertig: „Übrige" entfällt mit Hinweis
    r = query(repo, tmp_path, rows={"property": TAX, "top": 2, "other": True})
    assert len(r["rows"]) == 2
    assert any("Übrige" in n for n in r["provenance"]["notes"])
    assert any("ersten 2 von 5" in n for n in r["provenance"]["notes"])
    # einwertig: „Übrige" ist die Summe des Rests
    r = query(repo, tmp_path, rows={"property": LIC, "top": 3, "other": True, "missing": True})
    assert [x["kind"] for x in r["rows"]] == ["value"] * 3 + ["other", "missing"]
    assert sum(x["values"][0] for x in r["rows"]) == 400
    full = query(repo, tmp_path, rows={"property": LIC})
    assert len(full["rows"]) == len(LICENSES)


def test_formulas_chain_and_totals(repo, tmp_path):
    r = query(repo, tmp_path, rows={"property": TAX, "include": [M, P]}, series=[
        {"id": "anteil", "label": "OER-Anteil", "formula": "round(oer / gesamt * 100, 1)"},
        {"id": "gesamt", "label": "Gesamt"},
        {"id": "mit", "label": "mit Lizenz",
         "filters": [{"property": LIC, "values": [""]}]},
        {"id": "oer", "label": "OER",
         "filters": [{"property": "license", "values": ["OPEN", "CC_BY_OPEN"]}]},
        {"id": "ohne", "label": "ohne Lizenz", "formula": "gesamt - mit"},
        {"id": "doppelt", "label": "x2", "formula": "ohne * 2"},
    ])
    by_id = {c["id"]: pos for pos, c in enumerate(r["columns"])}
    row = next(x for x in r["rows"] if x["key"] == M)["values"]
    expect_ohne = repo.brute(lambda d: M in d[TAX] and not d[LIC])
    assert row[by_id["ohne"]] == expect_ohne
    assert row[by_id["doppelt"]] == 2 * expect_ohne
    oer = repo.brute(lambda d: M in d[TAX] and bool(OER & set(d[LIC])))
    assert row[by_id["anteil"]] == pytest.approx(
        round(oer / repo.brute(repo.has(TAX, M)) * 100, 1))
    totals = {c["id"]: c["total"] for c in r["columns"]}
    assert totals["ohne"] == repo.brute(lambda d: not d[LIC])
    assert [c["derived"] for c in r["columns"]] == [True, False, False, False, True, True]
    assert r["provenance"]["queries"] == 3
    assert r["provenance"]["columns"][4]["formula"] == "gesamt - mit"
    assert any("Formel" in n for n in r["provenance"]["notes"])


@pytest.mark.parametrize("series", [
    [{"id": "a", "label": "A", "formula": "a + 1"}],
    [{"id": "a", "label": "A", "formula": "b"}, {"id": "b", "label": "B", "formula": "a"}],
    [{"id": "a", "label": "A", "formula": "gibt_es_nicht * 2"}],
    [{"id": "a", "label": "A", "formula": "__import__('os')"}],
    [{"id": "a", "label": "A", "filters": [{"property": LVL, "values": [HS]}]}],  # Grundfilter
    [{"id": "a", "label": "A", "filters": [{"property": TAX, "values": [M]}]}],   # Zeilenfeld
])
def test_invalid_series(repo, tmp_path, series):
    with pytest.raises(QueryError):
        query(repo, tmp_path, filters=[{"property": LVL, "values": [S1]}],
              rows={"property": TAX}, series=series)
    assert repo.searches == []


def test_percent(repo, tmp_path):
    r = query(repo, tmp_path, percent="column", rows={"property": LIC, "missing": True})
    for row in r["rows"]:
        assert row["percent"] == [round(row["values"][0] / 400 * 100, 2)]
    assert sum(row["percent"][0] for row in r["rows"]) == pytest.approx(100, abs=0.1)
    r = query(repo, tmp_path, percent="row", rows={"property": LIC, "include": ["CC_BY"]},
              columns={"property": SRC})
    row = r["rows"][0]
    assert sum(row["percent"]) == pytest.approx(100, abs=0.05)
    assert sum(row["values"]) == repo.brute(repo.has(LIC, "CC_BY"))


def test_label_resolution_errors(repo, tmp_path):
    with pytest.raises(UnresolvedValue):
        query(repo, tmp_path, rows={"property": TAX, "include": ["Mathe"]})
    with pytest.raises(UnresolvedValue):
        query(repo, tmp_path, rows={"property": TAX, "groups": [
            {"label": "x", "values": ["Fysik"]}]})
    assert repo.searches == []


def test_structural_errors(repo, tmp_path):
    with pytest.raises(QueryError):
        query(repo, tmp_path, rows={"property": TAX}, columns={"property": TAX})
    with pytest.raises(QueryError):
        query(repo, tmp_path, filters=[{"property": LVL, "values": [S1]}],
              columns={"property": LVL})
    with pytest.raises(EduSharingError):  # keine Facette über Datensatz-Felder
        query(repo, tmp_path, rows={"property": "cclom:title"})
    with pytest.raises(EduSharingError):
        query(repo, tmp_path, columns={"property": "cclom:title"})
    with pytest.raises(QueryError):
        query(repo, tmp_path, rows={"property": TAX, "groups": [
            {"id": "a", "label": "1", "values": [M]}, {"id": "a", "label": "2", "values": [P]}]})
    assert repo.searches == []
    for bad in ({"rows": {"property": TAX, "other": True}},
                {"rows": {"property": TAX, "include": [M],
                          "groups": [{"label": "x", "values": [M]}]}},
                {"series": [{"id": "a", "label": "A"}], "columns": {"property": LVL}},
                {"series": [{"id": "a", "label": "A"}, {"id": "a", "label": "B"}]},
                {"series": [{"id": "Großes A", "label": "A"}]},
                {"series": [{"id": "a", "label": "A", "formula": "1",
                             "filters": [{"property": LVL, "values": [S1]}]}]},
                {"unbekannt": 1}):
        with pytest.raises(ValueError):
            QuerySpec(instance="t", **bad)


def test_call_budget(repo, tmp_path):
    groups = [{"label": f"g{i}", "values": [M]} for i in range(101)]
    series = [{"id": f"s{i}", "label": str(i),
               "filters": [{"property": SRC, "values": [f"x{i}"]}]} for i in range(4)]
    with pytest.raises(QueryError, match="Einzelabfragen"):
        query(repo, tmp_path, rows={"property": TAX, "groups": groups}, series=series)
    assert repo.searches == []


def test_oer_shape_costs_five_calls_per_sector(repo, tmp_path):
    """Form der OER-Statistik: Fächer × (Gesamt, A, B, C, mit Lizenz) plus D als Formel."""
    r = query(repo, tmp_path,
              filters=[{"property": LVL, "values": ["Hochschule"]}],
              rows={"property": TAX, "missing": True, "sort": "label"},
              series=[
                  {"id": "gesamt", "label": "Gesamt"},
                  {"id": "a", "label": "A", "filters": [
                      {"property": "license", "values": ["OPEN", "CC_BY_OPEN"]}]},
                  {"id": "b", "label": "B", "filters": [
                      {"property": "license", "values": ["CC_BY_RESTRICTED"]}]},
                  {"id": "mit", "label": "mit Lizenz", "filters": [
                      {"property": LIC, "values": [""]}]},
                  {"id": "c", "label": "C", "formula": "mit - a - b"},
                  {"id": "d", "label": "D", "formula": "gesamt - mit"},
              ])
    hs = lambda d: HS in d[LVL]  # noqa: E731
    totals = {c["id"]: c["total"] for c in r["columns"]}
    assert totals["d"] == repo.brute(lambda d: hs(d) and not d[LIC])
    assert totals["c"] == repo.brute(
        lambda d: hs(d) and bool({"COPYRIGHT_FREE", "CUSTOM"} & set(d[LIC])))
    assert totals["a"] + totals["b"] + totals["c"] + totals["d"] == totals["gesamt"]
    row = next(x for x in r["rows"] if x["key"] == M)
    by_id = {c["id"]: pos for pos, c in enumerate(r["columns"])}
    assert row["values"][by_id["d"]] == repo.brute(
        lambda d: hs(d) and M in d[TAX] and not d[LIC])
    # 4 gezählte Spalten, je eine Facetten-Abfrage plus eine für „ohne Wert"
    assert r["provenance"]["queries"] == 8


def test_cached_flag_and_refresh(repo, tmp_path):
    async def go():
        http = httpx.AsyncClient(transport=repo.transport())
        client = EduSharingClient(Instance(id="t", name="T", base_url="https://r.example"),
                                  http, TTLCache(ttl=900))
        vocab = VocabStore(client, tmp_path)
        spec = QuerySpec(instance="t", rows={"property": TAX})
        first = await run_query(spec, client, vocab)
        second = await run_query(spec, client, vocab)
        third = await run_query(spec.model_copy(update={"refresh": True}), client, vocab)
        return first, second, third

    first, second, third = asyncio.run(go())
    assert [x["provenance"]["cached"] for x in (first, second, third)] == [False, True, False]
    assert len(repo.searches) == 2


def test_upstream_failure_cancels_remaining_calls(tmp_path):
    repo = DataRepo()
    with pytest.raises(EduSharingError) as info:
        query(repo, tmp_path, rows={"property": TAX}, series=[
            {"id": "a", "label": "A"},
            {"id": "b", "label": "B", "filters": [{"property": "ccm:unbekannt",
                                                    "values": ["x"]}]}])
    assert info.value.kind == "unknown_property"
