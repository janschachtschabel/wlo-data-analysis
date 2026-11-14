"""Zeilen ausblenden: Sammelwerte wie „Allgemein“ sagen in einer Rangliste nichts."""
import pytest

from wda.engine.spec import QuerySpec

from .esdata import DataRepo
from .test_coverage import notes_of
from .test_engine import TAX, query


def test_rows_can_leave_out_collective_values(tmp_path):
    """Sammelwerte wie „Allgemein“ lassen sich ausblenden: Die Zeile fehlt, die Gesamtzahl
    bleibt die des Bestands, und ein Hinweis nennt, was fehlt."""
    repo = DataRepo()
    plain = query(repo, tmp_path, rows={"property": TAX})
    label = next(r["label"] for r in plain["rows"] if r["kind"] == "value")
    key = next(r["key"] for r in plain["rows"] if r["kind"] == "value")
    for given in (label, label.upper(), key):
        got = query(repo, tmp_path, rows={"property": TAX, "exclude": [given]})
        assert key not in {r["key"] for r in got["rows"]}
        assert len(got["rows"]) == len(plain["rows"]) - 1
        assert got["rows"][-1]["values"] == plain["rows"][-1]["values"]  # Gesamt bleibt
        assert label in notes_of(got)["rows_excluded"]
    # was nicht vorkommt, stört nicht und steht in keinem Hinweis
    same = query(repo, tmp_path, rows={"property": TAX, "exclude": ["gibt es nicht"]})
    assert len(same["rows"]) == len(plain["rows"]) and "rows_excluded" not in notes_of(same)
    # auch in der Wissenslandkarte
    topics = query(repo, tmp_path, rows={"property": TAX, "exclude": [label], "top": 10},
                   topics={"property": "cclom:general_keyword"})
    assert key not in {r["key"] for r in topics["rows"]}
    assert label in notes_of(topics)["rows_excluded"]
    for bad in ({"property": TAX, "exclude": [label], "top": 3, "other": True},
                {"property": TAX, "exclude": [label], "include": ["Mathematik"]},
                {"property": TAX, "exclude": [label], "rollup": "top"}):
        with pytest.raises(ValueError):
            QuerySpec(instance="t", rows=bad)
