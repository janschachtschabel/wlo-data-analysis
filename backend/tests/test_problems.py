"""Rangliste der größten Datenprobleme: fehlende Angaben und Werte außerhalb der Wertelisten
in einer Tabelle, das Größte zuerst."""
import pytest
from pydantic import ValidationError

from tests.test_coverage import LIC, LVL, PRICE, SRC, TAX, notes_of, query, repo  # noqa: F401
from wda.engine.spec import QuerySpec

FIELDS = [{"property": LVL}, {"property": TAX, "label": "Fach"}, {"property": PRICE}]


def test_problems_rank_missing_and_unknown_values_together(repo, tmp_path):  # noqa: F811
    result = query(repo, tmp_path, problems={"fields": FIELDS})
    total = len(repo.docs)
    assert [c["id"] for c in result["columns"]] == ["count", "share"]
    assert result["total_row"] is False and result["base_total"] == total
    rows = {r["key"]: r for r in result["rows"]}
    assert rows[f"{TAX}|missing"]["label"] == "Fach: ohne Angabe"
    missing = sum(1 for d in repo.docs if not d[TAX])
    assert rows[f"{TAX}|missing"]["values"] == [missing, round(100 * missing / total, 1)]
    bad = repo.brute(repo.has(LVL, "political"))
    assert rows[f"{LVL}|unknown"]["label"] == "Bildungsstufe: Wert außerhalb der Werteliste"
    assert rows[f"{LVL}|unknown"]["values"][0] == bad
    # das Größte zuerst; was nichts betrifft, steht nicht in der Liste
    counts = [r["values"][0] for r in result["rows"]]
    assert counts == sorted(counts, reverse=True) and all(counts)
    assert "problems" in notes_of(result)
    # dieselben Zahlen wie die beiden einzelnen Auswertungen
    fill = query(repo, tmp_path, fill={"fields": FIELDS})
    for row in fill["rows"]:
        if row["values"][1]:
            assert rows[f"{row['key']}|missing"]["values"][0] == row["values"][1]
    check = query(repo, tmp_path, vocab_check={"fields": FIELDS})
    for row in check["rows"]:
        if row["values"][3]:
            assert rows[f"{row['key']}|unknown"]["values"][0] == row["values"][3]


def test_problems_with_top_and_filters(repo, tmp_path):  # noqa: F811
    result = query(repo, tmp_path, problems={"fields": FIELDS, "top": 2})
    assert len(result["rows"]) == 2 and "rows_top" in notes_of(result)
    # ein Feld, das im Filter steht, hat dort immer einen Wert und wird nicht verglichen
    scoped = query(repo, tmp_path, filters=[{"property": SRC, "values": ["bpb_spider"]}],
                   problems={"fields": [*FIELDS, {"property": SRC}]})
    docs = [d for d in repo.docs if "bpb_spider" in d[SRC]]
    assert scoped["base_total"] == len(docs)
    assert not any(r["key"].startswith(SRC) for r in scoped["rows"])
    # Felder ohne Werteliste: nur „ohne Angabe“, kein Abbruch
    free = query(repo, tmp_path, problems={"fields": [{"property": SRC}, {"property": LIC}]})
    assert all(r["key"].endswith("|missing") for r in free["rows"])


def test_problems_stand_alone():
    for bad in ({"problems": {"fields": []}},
                {"problems": {"fields": [{"property": TAX}, {"property": TAX}]}},
                {"problems": {"fields": [{"property": TAX}]}, "rows": {"property": LVL}},
                {"problems": {"fields": [{"property": TAX}]},
                 "fill": {"fields": [{"property": LVL}]}},
                {"problems": {"fields": [{"property": TAX}], "top": 0}}):
        with pytest.raises(ValidationError):
            QuerySpec(instance="t", **bad)


def test_blank_values_count_once_and_undetermined_fields_stay_visible(repo, tmp_path):  # noqa: F811
    blank = 0
    for n, doc in enumerate(repo.docs):
        if doc[LVL] and n % 9 == 0:
            doc[LVL] = [" "]
            blank += 1
    assert blank
    result = query(repo, tmp_path, problems={"fields": [{"property": LVL}]})
    rows = {r["key"]: r["values"][0] for r in result["rows"]}
    without = sum(1 for d in repo.docs if not any(v.strip() for v in d[LVL]))
    assert rows[f"{LVL}|missing"] == without
    # der leere Wert steht nicht noch einmal als Wert außerhalb der Werteliste
    assert rows[f"{LVL}|unknown"] == repo.brute(repo.has(LVL, "political"))
    # gezählt sind Materialien, nicht Zuordnungen
    assert "Zuordnungen" not in " ".join(r["label"] for r in result["rows"])
