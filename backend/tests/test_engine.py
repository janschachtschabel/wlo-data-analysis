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
    assert {c["id"] for c in r["columns"]} <= {u for d in repo.docs for u in d[LVL]}  # Wert als ID
    totals = [c["total"] for c in r["columns"]]
    assert totals == sorted(totals, reverse=True)
    label_to_uri = {"Sekundarstufe I": S1, "Hochschule": HS, "Elementarbereich": EL,
                    "Primarstufe": EC + "grundschule"}
    for pos, col in enumerate(r["columns"]):
        uri = label_to_uri[col["label"]]
        assert col["total"] == repo.brute(repo.has(LVL, uri))
        assert cells(r, pos)[M] == repo.brute(lambda d, u=uri: u in d[LVL] and M in d[TAX])
    assert any("häufigsten" in n["text"] for n in r["provenance"]["notes"])
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
    assert got["g_mathe_und_deutsch"] == repo.brute(repo.has(TAX, M, D))
    assert got["g_fremd"] == 0
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
    assert r["provenance"]["notes"] == []   # einwertig: Summen sind exakt
    assert r["rows"][-1] == {"key": "__missing__", "label": "ohne Wert", "kind": "missing",
                             "values": [got["__missing__"]]}


def test_group_mode_explicit_overrides_auto(repo, tmp_path):
    groups = [{"id": "mp", "label": "M+P", "values": [M, P]}]
    summed = query(repo, tmp_path, rows={"property": TAX, "groups": groups,
                                         "group_mode": "sum"})
    assert cells(summed)["mp"] == repo.brute(repo.has(TAX, M)) + repo.brute(repo.has(TAX, P))
    assert any("zählt es mehrfach" in n["text"] for n in summed["provenance"]["notes"])
    before = len(repo.searches)
    with pytest.raises(QueryError, match="einwertig"):  # einwertiges Feld verträgt kein ODER
        query(repo, tmp_path, rows={"property": LIC, "group_mode": "distinct", "groups": [
            {"label": "x", "values": ["CC_0", "PDM"]}]})
    assert len(repo.searches) == before


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
    assert any("nur die gefilterten" in n["text"] for n in r["provenance"]["notes"])
    for rows in ({"property": TAX, "missing": True},
                 {"property": TAX, "top": 1, "other": True},
                 {"property": TAX, "groups": [{"label": "x", "values": [M]}]},
                 {"property": TAX, "group_mode": "sum",
                  "groups": [{"label": "x", "values": [D]}]}):
        with pytest.raises(QueryError):
            query(repo, tmp_path, filters=[{"property": TAX, "values": [M]}], rows=rows)


def test_include_sort_top_other(repo, tmp_path):
    r = query(repo, tmp_path, rows={"property": TAX, "include": ["Physik", "Mathematik"]})
    assert [x["label"] for x in r["rows"]] == ["Physik", "Mathematik"]
    r = query(repo, tmp_path, rows={"property": TAX, "sort": "label"})
    labels = [x["label"] for x in r["rows"]]
    assert labels == sorted(labels, key=str.casefold) and len(labels) == 5
    # mehrwertig: „Übrige" ist eine eigene Zählung, keine Summe der restlichen Zeilen
    r = query(repo, tmp_path, rows={"property": TAX, "top": 2, "other": True})
    assert [x["kind"] for x in r["rows"]] == ["value", "value", "other"]
    rest = [u for u in (M, P, D, DISC + "240", DISC + "320")
            if u not in {x["key"] for x in r["rows"]}]
    assert r["rows"][-1]["values"] == [repo.brute(repo.has(TAX, *rest))]
    assert r["rows"][-1]["values"][0] < sum(repo.brute(repo.has(TAX, u)) for u in rest)
    assert any("Übrige" in n["text"] for n in r["provenance"]["notes"])
    assert any("ersten 2 von 5" in n["text"] for n in r["provenance"]["notes"])
    assert r["provenance"]["queries"] == 2
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
    assert any("Formel" in n["text"] for n in r["provenance"]["notes"])


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
    assert all(v is not None for row in r["rows"] for v in row["values"])


def test_license_key_prefix_is_never_silently_wrong(repo, tmp_path):
    """Der Lizenzschlüssel trifft als Kriterium per Präfix: CC_BY zählte alle CC_BY… mit."""
    cc_by = {"property": LIC, "values": ["CC_BY"]}
    for spec in (
        {"filters": [cc_by]},
        {"series": [{"id": "a", "label": "A", "filters": [cc_by]}]},
        {"columns": {"property": LIC}},
        {"columns": {"property": LIC, "include": ["CC_BY_NC"]}},
        {"filters": [cc_by], "rows": {"property": LIC}},
    ):
        with pytest.raises(QueryError, match="mitzählen"):
            query(repo, tmp_path, **spec)
    with pytest.raises(QueryError, match="nur einen Wert"):
        query(repo, tmp_path, filters=[{"property": LIC, "values": ["CC_0", "PDM"]}])
    # Schlüssel ohne längere Namensvettern sind exakt und erlaubt
    r = query(repo, tmp_path, rows={"property": TAX}, series=[
        {"id": "cc0", "label": "CC0", "filters": [{"property": LIC, "values": ["CC_0"]}]},
        {"id": "mit", "label": "mit", "filters": [{"property": LIC, "values": [""]}]}])
    assert r["columns"][0]["total"] == repo.brute(repo.has(LIC, "CC_0"))
    assert r["columns"][1]["total"] == repo.brute(lambda d: bool(d[LIC]))
    assert any("leere Wert" in n["text"] for n in r["provenance"]["notes"])
    # der exakte Weg: Lizenz als Zeilen, das andere Feld als Spalten
    r = query(repo, tmp_path, rows={"property": LIC}, columns={"property": SRC})
    col = next(pos for pos, c in enumerate(r["columns"]) if c["label"] == "oersi_spider")
    assert cells(r, col)["CC_BY"] == repo.brute(
        lambda d: "CC_BY" in d[LIC] and "oersi_spider" in d[SRC])


def test_field_that_is_multi_valued_only_in_the_data(tmp_path):
    """Die Quelle hat im Metadatenset keinen Mehrwert-Typ, manche Dokumente tragen aber
    mehrere. Nichts darf als Summe gezählt werden."""
    from .esdata import make_docs
    docs = make_docs()
    for pos, doc in enumerate(docs):
        if pos % 3 == 0:
            doc[SRC] = ["oersi_spider", "youtube_spider", "bpb_spider"][: 2 + pos % 2]
        if pos % 10 == 1:
            doc[SRC] = []
    repo = DataRepo(docs)
    r = query(repo, tmp_path, rows={"property": SRC, "top": 1, "other": True, "missing": True})
    first = r["rows"][0]["key"]
    rest = [s for s in ("oersi_spider", "youtube_spider", "bpb_spider") if s != first]
    got = cells(r)
    assert got["__other__"] == repo.brute(repo.has(SRC, *rest))
    assert got["__missing__"] == repo.brute(lambda d: not d[SRC]) == 40
    r = query(repo, tmp_path, rows={"property": SRC, "groups": [
        {"id": "g", "label": "zwei", "values": ["oersi_spider", "bpb_spider"]}]})
    assert cells(r)["g"] == repo.brute(repo.has(SRC, "oersi_spider", "bpb_spider"))


def test_overlapping_sum_groups_and_other(repo, tmp_path):
    r = query(repo, tmp_path, rows={"property": LIC, "top": 1, "other": True, "groups": [
        {"id": "a", "label": "a", "values": ["CUSTOM"]},
        {"id": "b", "label": "b", "values": ["CC_0"]},
        {"id": "c", "label": "c", "values": ["CC_0", "CC_BY", "CUSTOM"]}]})
    assert cells(r)["__other__"] == repo.brute(repo.has(LIC, "CC_0", "CC_BY"))


def test_huge_numbers_do_not_crash(repo, tmp_path):
    nines = "9" * 400
    r = query(repo, tmp_path, percent="row", rows={"property": TAX}, series=[
        {"id": "a", "label": "A"},
        {"id": "b", "label": "B", "formula": f"a * {nines}"},
        {"id": "c", "label": "C", "formula": f"round(a * {nines}, 2)"},
        {"id": "d", "label": "D", "formula": "a * 1000000000000000000"}])
    assert all(row["values"][1:] == [None, None, None] for row in r["rows"])
    assert [c["total"] for c in r["columns"]][1:] == [None, None, None]


def test_percent_skips_derived_columns(repo, tmp_path):
    series = [{"id": "alle", "label": "Alle"},
              {"id": "oer", "label": "OER", "filters": [
                  {"property": "license", "values": ["OPEN", "CC_BY_OPEN"]}]},
              {"id": "quote", "label": "Quote", "formula": "round(oer / alle * 100, 1)"}]
    r = query(repo, tmp_path, percent="column", rows={"property": TAX}, series=series)
    assert all(row["percent"][2] is None and row["percent"][0] is not None
               for row in r["rows"])
    r = query(repo, tmp_path, percent="row", rows={"property": TAX}, series=series)
    assert all(row["percent"][2] is None for row in r["rows"])
    assert any("nicht überschneiden" in n["text"] for n in r["provenance"]["notes"])


def test_truncated_facet_gives_unknown_not_zero(tmp_path):
    from .esdata import make_docs
    docs = make_docs()
    for pos, doc in enumerate(docs):
        doc[SRC] = [f"quelle_{pos % 30:02d}"]  # 30 Werte, je 13 oder 14 Dokumente
    docs.extend({TAX: [], LVL: [], LIC: [], SRC: ["selten"]} for _ in range(1))
    repo = DataRepo(docs)

    async def go():
        http = httpx.AsyncClient(transport=repo.transport())
        client = EduSharingClient(Instance(id="t", name="T", base_url="https://r.example"),
                                  http, TTLCache(ttl=0))
        original = client.aggregate

        async def small(criteria, facets=(), **kw):  # Facetten künstlich kürzen (5 × 2 Werte)
            kw["facet_limit"] = 2
            return await original(criteria, facets, **kw)

        client.aggregate = small
        vocab = VocabStore(client, tmp_path)
        return await run_query(QuerySpec(instance="t", rows={
            "property": SRC, "include": ["quelle_00", "selten"], "missing": True}), client, vocab)

    r = asyncio.run(go())
    got = cells(r)
    assert got["quelle_00"] == 14 and got["selten"] is None
    assert got["__missing__"] is None
    assert any("gekürzt" in n["text"] for n in r["provenance"]["notes"])


def test_budget_is_checked_before_any_call(repo, tmp_path):
    groups = [{"label": f"g{i}", "values": [M]} for i in range(60)]
    with pytest.raises(QueryError, match="Einzelabfragen"):
        query(repo, tmp_path, rows={"property": TAX, "groups": groups},
              columns={"property": LVL, "top": 12})
    assert repo.searches == []


def test_function_names_are_not_column_ids():
    for name in ("round", "max", "min"):
        with pytest.raises(ValueError):
            QuerySpec(instance="t", series=[{"id": name, "label": "x"}])


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


def test_notes_name_ungrouped_values_and_overlapping_rows(repo, tmp_path):
    """Befund aus dem Live-Test: Tabellen, deren Zeilen sich nicht zur Gesamtzahl summieren,
    sagen das."""
    r = query(repo, tmp_path, rows={"property": LIC, "groups": [
        {"id": "a", "label": "A", "values": ["CC_0", "PDM", "CC_BY", "CC_BY_SA"]}]})
    loose = [n for n in r["provenance"]["notes"] if "in keiner Gruppe" in n["text"]]
    occurring = {v for d in repo.docs for v in d[LIC]} - OER
    assert len(loose) == 1 and loose[0]["text"].startswith(f"{len(occurring)} vorkommende Werte")
    r = query(repo, tmp_path, rows={"property": TAX, "groups": [
        {"label": "Mathe", "values": [M]}]})
    assert any("in keiner Gruppe" in n["text"] for n in r["provenance"]["notes"])
    # mehrwertiges Feld als Zeilen: Hinweis auf Überschneidung; einwertig: keiner
    r = query(repo, tmp_path, rows={"property": TAX})
    assert any("in mehreren Zeilen" in n["text"] for n in r["provenance"]["notes"])
    r = query(repo, tmp_path, rows={"property": LIC})
    assert not any("in mehreren Zeilen" in n["text"] for n in r["provenance"]["notes"])


def test_prefix_message_for_columns_does_not_talk_about_a_filter(repo, tmp_path):
    with pytest.raises(QueryError, match="taugt nicht als Spaltenfeld") as info:
        query(repo, tmp_path, columns={"property": LIC})
    assert "Filter" not in str(info.value)


def test_result_carries_what_a_stored_run_needs(repo, tmp_path):
    """Aus dem übergreifenden Review: Ein gespeichertes Ergebnis muss für sich verständlich
    und mit späteren Läufen vergleichbar sein."""
    r = query(repo, tmp_path, refresh=True, rows={"property": TAX, "groups": [
        {"label": "MINT Fächer", "values": ["Mathematik", "Physik"]},
        {"id": "deutsch", "label": "Deutsch", "values": ["Deutsch"]}]})
    assert r["schema_version"] == 1
    prov = r["provenance"]
    assert prov["instance"] == {"id": "t", "base_url": "https://r.example",
                                "repository": "-home-", "metadataset": "mds_oeh",
                                "query": "ngsearch"}
    assert "refresh" not in prov["spec"] and prov["spec"]["rows"]["property"] == TAX
    assert prov["rows"] == {"property": TAX, "mode": "distinct", "include": None, "groups": [
        {"key": "g_mint_f_cher", "label": "MINT Fächer", "values": sorted([M, P])},
        {"key": "deutsch", "label": "Deutsch", "values": [D]}]}
    assert all(set(n) == {"code", "text"} for n in prov["notes"])
    assert "values_ungrouped" in {n["code"] for n in prov["notes"]}
    # Gruppen-IDs hängen am Namen, nicht an der Position
    swapped = query(repo, tmp_path, rows={"property": TAX, "groups": [
        {"id": "deutsch", "label": "Deutsch", "values": ["Deutsch"]},
        {"label": "MINT Fächer", "values": ["Mathematik", "Physik"]}]})
    assert cells(swapped) == cells(r)
    assert query(repo, tmp_path)["provenance"]["rows"] is None
    with pytest.raises(QueryError, match="eindeutig"):
        query(repo, tmp_path, rows={"property": TAX, "groups": [
            {"label": "A b", "values": [M]}, {"label": "a-B", "values": [P]}]})


def test_include_map_tells_which_input_became_which_row(repo, tmp_path):
    r = query(repo, tmp_path, rows={"property": TAX, "sort": "given",
                                    "include": ["Physik", M, "Mathematik", "Deutsch"]})
    assert r["provenance"]["rows"]["include"] == [P, M, M, D]
    assert [x["key"] for x in r["rows"]] == [P, M, D]
