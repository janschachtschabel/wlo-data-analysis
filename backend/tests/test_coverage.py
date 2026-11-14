"""Auswahl unter den vorkommenden Werten (match, vocab), „ohne Wert“ bei einwertigen Feldern,
die das Repositorium nicht als Filter zählt, und der Abgleich mit dem Vokabular."""

import pytest
from pydantic import ValidationError

from wda.engine.formula import QueryError
from wda.engine.spec import QuerySpec

from .esdata import DISC, EC, HSF, LEVELS, UNI_SUBJECTS, DataRepo
from .test_engine import LIC, LVL, SRC, TAX, cells, query

UNI = set(UNI_SUBJECTS.values())
OFF = HSF + "n999"  # ein Hochschulfach, das die Werteliste nicht kennt
PRICE, BUFFET = "ccm:price", "ccm:oeh_buffet_criteria"


@pytest.fixture
def repo():
    repo = DataRepo()  # zwei Vokabulare in einem Feld, wie bei WLO
    for n, doc in enumerate(repo.docs):
        if n % 7 == 0:
            doc[TAX] = [*doc[TAX], OFF]
    return repo


def notes_of(result):
    return {n["code"]: n["text"] for n in result["provenance"]["notes"]}


def test_rows_match_keeps_one_vocabulary(repo, tmp_path):
    school = cells(query(repo, tmp_path, rows={"property": TAX, "match": DISC}))
    uni = query(repo, tmp_path, rows={"property": TAX, "match": HSF})
    assert school and all(k.startswith(DISC) for k in school)
    assert set(cells(uni)) == UNI | {OFF}
    for key, value in {**school, **cells(uni)}.items():
        assert value == repo.brute(repo.has(TAX, key))
    assert "4 von 9" in notes_of(uni)["rows_selected"]
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
    both = cells(query(repo, tmp_path, rows={"property": TAX, "vocab": "outside", "match": HSF}))
    assert set(both) == {OFF}


def test_vocab_comparison_only_for_fields_with_a_value_list(repo, tmp_path):
    """Bei freiem Text wäre „nicht im Vokabular“ eine Liste von Werten einzelner Datensätze."""
    for bad in ({"rows": {"property": SRC, "vocab": "outside"}},
                {"rows": {"property": LIC, "vocab": "inside"}},
                {"vocab_check": {"fields": [{"property": SRC}, {"property": "cclom:title"}]}}):
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
    off = sum(1 for d in repo.docs if OFF in d[TAX])
    assert rows[TAX]["values"][1] == 1 and rows[TAX]["values"][3] == off
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


def test_vocab_check_leaves_fields_without_value_list_empty_and_never_asks(repo, tmp_path):
    """Ein mitgeliefertes Dashboard läuft auch auf einer Instanz, die ein Feld nicht kennt."""
    result = query(repo, tmp_path, vocab_check={"fields": [
        {"property": LVL}, {"property": "cclom:title"}, {"property": "gibt:esnicht"}]})
    rows = {r["key"]: r["values"] for r in result["rows"]}
    assert rows[LVL][0] and rows["cclom:title"] == [None] * 5 == rows["gibt:esnicht"]
    assert "Titel" in notes_of(result)["vocab_closed"]
    sent = str(repo.searches)
    assert "cclom:title" not in sent and "gibt:esnicht" not in sent


def test_fill_per_row_respects_match(repo, tmp_path):
    result = query(repo, tmp_path, rows={"property": LVL, "match": EC},
                   fill={"fields": [{"property": TAX}]})
    assert {r["key"] for r in result["rows"]} == set(LEVELS.values())


def test_fill_counts_a_field_whose_filter_also_finds_other_spellings(repo, tmp_path):
    lang = "cclom:general_language"
    for n, doc in enumerate(repo.docs):
        if n % 9 == 0:
            doc[lang] = ["DE"]
        elif n % 11 == 0:
            doc[lang] = ["de-DE", "en"]
    result = query(repo, tmp_path, fill={"fields": [{"property": lang}]})
    filled = repo.brute(lambda d: bool(d.get(lang)))
    assert result["rows"][0]["values"][0] == filled
    assert "fill_range" not in notes_of(result)


@pytest.fixture
def tree(repo, monkeypatch):
    """Hochschulfächer als Baum: Wurzel, Fächergruppen, darunter ein Studienbereich."""
    from . import esdata
    child = HSF + "n8-61"
    widgets = []
    for widget in esdata.MDS["widgets"]:
        if widget["id"] == TAX:
            values = [dict(v, parent=HSF if v["id"].startswith(HSF) else None)
                      for v in widget["values"]]
            values += [{"id": HSF, "caption": "Destatis-Systematik", "parent": None},
                       {"id": child, "caption": "Informatik", "parent": HSF + "n8"}]
            widget = dict(widget, values=values)
        widgets.append(widget)
    monkeypatch.setattr(esdata, "MDS", dict(esdata.MDS, widgets=widgets))
    for n, doc in enumerate(repo.docs):
        if n % 5 == 0:
            doc[TAX] = [*doc[TAX], child]
        if n % 13 == 0:
            doc[TAX] = [*doc[TAX], HSF]
    return child


def test_rollup_counts_each_top_value_with_everything_below(repo, tree, tmp_path):
    result = query(repo, tmp_path, rows={"property": TAX, "match": HSF, "rollup": "top"})
    got = cells(result)
    assert set(got) == UNI  # die Wurzel und der Studienbereich sind keine Zeilen
    eng = HSF + "n8"
    assert got[eng] == repo.brute(repo.has(TAX, eng, tree))
    assert got[eng] > repo.brute(repo.has(TAX, eng))
    assert got[HSF + "n1"] == repo.brute(repo.has(TAX, HSF + "n1"))
    labels = {r["key"]: r["label"] for r in result["rows"]}
    assert labels[eng] == "Ingenieurwissenschaften"
    # größte zuerst
    values = [r["values"][0] for r in result["rows"]]
    assert values == sorted(values, reverse=True)
    notes = notes_of(result)
    assert "Destatis-Systematik" in notes["rows_rollup"]
    # die Wurzel und der unbekannte Wert kommen vor, stehen aber in keiner Zeile
    assert "2 vorkommende Werte" in notes["values_ungrouped"]


def test_rollup_with_columns_counts_cells_and_sorts_by_row_sum(repo, tree, tmp_path):
    result = query(repo, tmp_path, rows={"property": TAX, "match": HSF, "rollup": "top"},
                   columns={"property": LVL, "match": EC})
    eng = next(r for r in result["rows"] if r["key"] == HSF + "n8")
    for col, value in zip(result["columns"], eng["values"], strict=True):
        assert value == repo.brute(lambda d, c=col["id"]: bool(
            {HSF + "n8", tree} & set(d[TAX])) and c in d[LVL])
    sums = [sum(r["values"]) for r in result["rows"]]
    assert sums == sorted(sums, reverse=True)


def test_matrix_rows_are_ordered_by_their_sum_not_by_the_first_column(repo, tmp_path):
    result = query(repo, tmp_path, rows={"property": TAX, "match": DISC},
                   columns={"property": LVL, "match": EC})
    sizes = [repo.brute(repo.has(TAX, r["key"])) for r in result["rows"]]
    assert sizes == sorted(sizes, reverse=True)  # nach der Zahl der Materialien je Zeile
    first = [r["values"][0] for r in result["rows"]]
    assert first != sorted(first, reverse=True)  # sonst prüft der Test nichts


def test_rollup_needs_a_value_list_and_excludes_what_does_not_fit(repo, tmp_path):
    with pytest.raises(QueryError, match="Werteliste"):
        query(repo, tmp_path, rows={"property": SRC, "rollup": "top"})
    for rows in ({"property": TAX, "rollup": "top", "vocab": "inside"},
                 {"property": TAX, "rollup": "top", "include": ["x"]}):
        with pytest.raises(ValidationError):
            QuerySpec(instance="x", rows=rows)
    with pytest.raises(ValidationError):
        QuerySpec(instance="x", rows={"property": TAX, "rollup": "top"},
                  fill={"fields": [{"property": LIC}]})
