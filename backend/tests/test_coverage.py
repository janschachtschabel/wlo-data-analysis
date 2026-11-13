"""Auswahl unter den vorkommenden Werten (match, vocab), „ohne Wert“ bei einwertigen Feldern,
die das Repositorium nicht als Filter zählt, und der Abgleich mit dem Vokabular."""

import pytest
from pydantic import ValidationError

from wda.engine.formula import QueryError
from wda.engine.spec import QuerySpec

from .esdata import DISC, EC, LEVELS, DataRepo
from .test_engine import LIC, LVL, SRC, TAX, cells, query

HSF = "http://w3id.org/openeduhub/vocabs/hochschulfaechersystematik/"
PRICE, BUFFET = "ccm:price", "ccm:oeh_buffet_criteria"


@pytest.fixture
def repo():
    repo = DataRepo()
    # zwei Vokabulare in einem Feld, wie bei WLO: Schulfächer und Hochschulfächer
    for n, doc in enumerate(repo.docs):
        if n % 3 == 0:
            doc[TAX] = [*doc[TAX], HSF + ("n01" if n % 2 else "n02")]
    return repo


def notes_of(result):
    return {n["code"]: n["text"] for n in result["provenance"]["notes"]}


def test_rows_match_keeps_one_vocabulary(repo, tmp_path):
    school = cells(query(repo, tmp_path, rows={"property": TAX, "match": DISC}))
    uni = query(repo, tmp_path, rows={"property": TAX, "match": HSF})
    assert school and all(k.startswith(DISC) for k in school)
    assert set(cells(uni)) == {HSF + "n01", HSF + "n02"}
    for key, value in {**school, **cells(uni)}.items():
        assert value == repo.brute(repo.has(TAX, key))
    assert "2 von" in notes_of(uni)["rows_selected"]
    # top wirkt nach der Auswahl
    top = query(repo, tmp_path, rows={"property": TAX, "match": HSF, "top": 1})
    assert len(top["rows"]) == 1 and top["rows"][0]["key"].startswith(HSF)


def test_matrix_of_subjects_by_level_with_match_on_both_axes(repo, tmp_path):
    result = query(repo, tmp_path, rows={"property": TAX, "match": HSF},
                   columns={"property": LVL, "match": EC, "top": 40})
    ids = [c["id"] for c in result["columns"]]
    assert ids and all(i.startswith(EC) for i in ids) and "political" not in ids
    assert set(ids) == set(LEVELS.values())
    for row in result["rows"]:
        for col, value in zip(ids, row["values"], strict=True):
            assert value == repo.brute(
                lambda d, r=row["key"], c=col: r in d.get(TAX, []) and c in d.get(LVL, []))


def test_rows_outside_and_inside_the_vocabulary(repo, tmp_path):
    outside = cells(query(repo, tmp_path, rows={"property": LVL, "vocab": "outside"}))
    assert outside == {"political": repo.brute(repo.has(LVL, "political"))}
    inside = cells(query(repo, tmp_path, rows={"property": LVL, "vocab": "inside"}))
    assert set(inside) == set(LEVELS.values())
    # die Hochschulfächer stehen nicht in der Werteliste des Testbestands
    both = cells(query(repo, tmp_path, rows={"property": TAX, "vocab": "outside", "match": HSF}))
    assert set(both) == {HSF + "n01", HSF + "n02"}


def test_vocab_comparison_only_for_fields_with_a_value_list(repo, tmp_path):
    """Bei freiem Text wäre „nicht im Vokabular“ eine Liste von Werten einzelner Datensätze."""
    for bad in ({"rows": {"property": SRC, "vocab": "outside"}},
                {"rows": {"property": LIC, "vocab": "inside"}},
                {"vocab_check": {"fields": [{"property": SRC}]}},
                {"vocab_check": {"fields": [{"property": LVL}, {"property": "cclom:title"}]}}):
        before = len(repo.searches)
        with pytest.raises(QueryError, match="Werteliste"):
            query(repo, tmp_path, **bad)
        assert len(repo.searches) == before  # nichts gefragt


def test_spec_rejects_what_does_not_fit_together():
    for bad in ({"rows": {"property": TAX, "match": "x", "missing": True}},
                {"rows": {"property": TAX, "vocab": "outside", "top": 3, "other": True}},
                {"rows": {"property": TAX, "vocab": "outside", "include": ["a"]}},
                {"rows": {"property": TAX, "vocab": "sonstwas"}},
                {"rows": {"property": TAX, "match": ""}},
                {"columns": {"property": LVL, "match": "x", "include": ["a"]},
                 "rows": {"property": TAX}},
                {"vocab_check": {"fields": []}},
                {"vocab_check": {"fields": [{"property": TAX}, {"property": TAX}]}},
                {"vocab_check": {"fields": [{"property": TAX}]}, "rows": {"property": LVL}},
                {"vocab_check": {"fields": [{"property": TAX}]},
                 "fill": {"fields": [{"property": LVL}]}}):
        with pytest.raises(ValidationError):
            QuerySpec(instance="t", **bad)


def test_vocab_check_counts_values_and_uses(repo, tmp_path):
    result = query(repo, tmp_path, vocab_check={"fields": [
        {"property": LVL}, {"property": TAX, "label": "Fächer"}, {"property": PRICE}]})
    assert result["total_row"] is False and result["base_total"] == len(repo.docs)
    assert [c["id"] for c in result["columns"]] == [
        "values", "unknown_values", "uses", "unknown_uses", "share"]
    rows = {r["key"]: r for r in result["rows"]}
    assert rows[TAX]["label"] == "Fächer" and rows[LVL]["label"] == "Bildungsstufe"
    uses = sum(len(d[LVL]) for d in repo.docs)
    bad = repo.brute(repo.has(LVL, "political"))
    assert rows[LVL]["values"] == [len(LEVELS) + 1, 1, uses, bad, round(100 * bad / uses, 1)]
    uni = sum(1 for d in repo.docs for v in d[TAX] if v.startswith(HSF))
    assert rows[TAX]["values"][1] == 2 and rows[TAX]["values"][3] == uni
    assert rows[PRICE]["values"][1:2] == [0] and rows[PRICE]["values"][3] == 0
    assert "vocab_check" in notes_of(result)
    assert result["provenance"]["queries"] == 4  # je Feld einer und die Grundgesamtheit


def test_vocab_check_respects_base_filters(repo, tmp_path):
    result = query(repo, tmp_path, filters=[{"property": SRC, "values": ["bpb_spider"]}],
                   vocab_check={"fields": [{"property": LVL}]})
    docs = [d for d in repo.docs if "bpb_spider" in d[SRC]]
    assert result["base_total"] == len(docs)
    assert result["rows"][0]["values"][3] == sum(1 for d in docs if "political" in d[LVL])
    with pytest.raises(QueryError, match="Filtern"):
        query(repo, tmp_path,
              filters=[{"property": LVL, "values": [{"value": "political", "literal": True}]}],
              vocab_check={"fields": [{"property": LVL}]})


def test_missing_row_for_single_valued_field_the_repository_does_not_filter(repo, tmp_path):
    result = query(repo, tmp_path, rows={"property": PRICE, "missing": True})
    got = cells(result)
    assert got["__missing__"] == sum(1 for d in repo.docs if not d[PRICE])
    assert sum(got.values()) == len(repo.docs)
    assert "rows_overlap" not in notes_of(result)  # einwertig: jede Zeile zählt Materialien
    # mehrwertig und nicht als Filter zählbar: „ohne Wert“ bleibt unbekannt, kein Abbruch
    multi = query(repo, tmp_path, rows={"property": BUFFET, "missing": True})
    assert cells(multi)["__missing__"] is None
    assert "missing_unknown" in notes_of(multi) and "rows_overlap" in notes_of(multi)
