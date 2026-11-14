"""Lückenliste: aus einer Matrix wird eine Liste der Felder unter der Erwartung."""

import pytest
from pydantic import ValidationError

from wda.engine.engine import _gaps
from wda.engine.spec import QuerySpec

ROWS = {"property": "ccm:taxonid"}
COLS = {"property": "ccm:educationalcontext"}


def matrix():
    return {
        "columns": [{"id": "s1", "label": "Sek I", "derived": False, "total": 150},
                    {"id": "s2", "label": "Sek II", "derived": False, "total": 110},
                    {"id": "hs", "label": "Hochschule", "derived": False, "total": 60}],
        "rows": [{"key": "m", "label": "Mathe", "kind": "value", "values": [100, 100, 40]},
                 {"key": "k", "label": "Kunst", "kind": "value", "values": [50, 0, None]},
                 {"key": "g", "label": "Gruppe", "kind": "group", "values": [0, 10, 20]},
                 {"key": "__other__", "label": "Übrige", "kind": "other", "values": [5, 5, 5]}],
        "provenance": {"notes": [], "columns": []}}


def spec(**gaps):
    gaps.setdefault("below", 100)
    return QuerySpec(instance="t", rows=ROWS, columns=COLS, gaps=gaps)


def note(out):
    return next(n["text"] for n in out["provenance"]["notes"] if n["code"] == "gaps")


def test_cells_are_ranked_by_share_of_the_expected():
    out = _gaps(matrix(), spec(min_expected=1))
    # Summen: Mathe 240, Kunst 50, Gruppe 30; Sek I 150, Sek II 110, Hochschule 60; alles 320
    first = out["rows"][0]
    assert first["label"] == "Kunst · Sek II" and first["values"] == [0, 17, 0.0]
    assert first["key"] == '["k", "s2"]'
    assert out["rows"][1]["label"] == "Gruppe · Sek I"  # Gruppen (rollup) zählen mit
    # nur Felder unter der Erwartung; „Übrige“ zählt nicht mit
    assert all(r["values"][2] < 100 for r in out["rows"])
    assert not any("Übrige" in r["label"] for r in out["rows"])
    assert out["total_row"] is False
    assert [c["id"] for c in out["provenance"]["columns"]] == ["count", "expected", "ratio"]


def test_an_unknown_cell_is_not_a_gap():
    out = _gaps(matrix(), spec(min_expected=1))
    assert not any(r["label"] == "Kunst · Hochschule" for r in out["rows"])
    assert "1 Felder ließen sich nicht bestimmen" in note(out)


def test_small_expectations_and_the_limit():
    big = [r["label"] for r in _gaps(matrix(), spec(min_expected=40))["rows"]]
    # gleicher Anteil: größere Erwartung zuerst
    assert big == ["Mathe · Sek I", "Mathe · Hochschule"]
    one = _gaps(matrix(), spec(min_expected=1, limit=1))
    assert len(one["rows"]) == 1 and "1 von " in note(one)
    empty = {**matrix(), "rows": []}
    assert _gaps(empty, spec())["rows"] == []


def test_gaps_needs_a_matrix():
    with pytest.raises(ValidationError):
        QuerySpec(instance="t", rows=ROWS, gaps={})
    with pytest.raises(ValidationError):
        QuerySpec(instance="t", rows=ROWS, columns=COLS, gaps={}, percent="row")


def test_only_cells_far_below_the_expected_by_default():
    out = _gaps(matrix(), QuerySpec(instance="t", rows=ROWS, columns=COLS,
                                    gaps={"min_expected": 1}))
    assert out["rows"] and all(r["values"][2] < 50 for r in out["rows"])
    assert "unter 50 % der Erwartung" in note(out)
