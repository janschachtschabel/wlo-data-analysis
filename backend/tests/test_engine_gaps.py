"""Lückenliste: aus einer Matrix wird eine Liste der Felder unter der Erwartung."""

import pytest
from pydantic import ValidationError

from wda.engine.engine import _gaps
from wda.engine.spec import QuerySpec

ROWS = {"property": "ccm:taxonid"}
COLS = {"property": "ccm:educationalcontext"}


def matrix():
    return {
        "columns": [{"id": "s1", "label": "Sek I", "derived": False, "total": 110},
                    {"id": "s2", "label": "Sek II", "derived": False, "total": 110}],
        "rows": [{"key": "m", "label": "Mathe", "kind": "value", "values": [100, 100]},
                 {"key": "k", "label": "Kunst", "kind": "value", "values": [10, None]},
                 {"key": "__other__", "label": "Übrige", "kind": "other", "values": [5, 5]}],
        "provenance": {"notes": [], "columns": []}}


def spec(**gaps):
    return QuerySpec(instance="t", rows=ROWS, columns=COLS, gaps=gaps)


def test_cells_are_ranked_by_share_of_the_expected():
    out = _gaps(matrix(), spec(min_expected=1))
    # Kunst · Sek II: erwartet 10 * 100 / 210, gezählt 0
    assert out["rows"][0]["label"] == "Kunst · Sek II"
    assert out["rows"][0]["values"] == [0, 5, 0.0]
    assert out["rows"][0]["key"] == "k|s2"
    assert len(out["rows"]) == 4 and out["total_row"] is False  # „Übrige“ zählt nicht mit
    assert [c["id"] for c in out["provenance"]["columns"]] == ["count", "expected", "ratio"]


def test_small_expectations_and_the_limit():
    assert [r["key"] for r in _gaps(matrix(), spec(min_expected=20))["rows"]] == [
        "m|s1", "m|s2"]
    assert len(_gaps(matrix(), spec(min_expected=1, limit=1))["rows"]) == 1
    empty = {**matrix(), "rows": []}
    assert _gaps(empty, spec())["rows"] == []


def test_gaps_needs_a_matrix():
    with pytest.raises(ValidationError):
        QuerySpec(instance="t", rows=ROWS, gaps={})
    with pytest.raises(ValidationError):
        QuerySpec(instance="t", rows=ROWS, columns=COLS, gaps={}, percent="row")
