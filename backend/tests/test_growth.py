"""Zuwachs je Monat: wie viele der heutigen Materialien kamen in welchem Monat herein."""
from datetime import UTC, date, datetime

import pytest
from pydantic import ValidationError

from tests.esdata import DataRepo
from tests.test_engine import SRC, TAX, query
from wda.engine.formula import QueryError
from wda.engine.spec import QuerySpec


def ms(year, month, day):
    return str(int(datetime(year, month, day, 12, tzinfo=UTC).timestamp() * 1000))


@pytest.fixture
def repo(monkeypatch):
    monkeypatch.setattr("wda.filters._today", lambda: date(2026, 11, 14))
    repo = DataRepo()
    days = [(2026, 11, 3), (2026, 10, 20), (2026, 10, 1), (2026, 8, 31), (2025, 12, 5),
            (2024, 1, 1)]
    for n, doc in enumerate(repo.docs):
        doc["_cm:created"] = [ms(*days[n % len(days)])]
        doc["_cm:modified"] = [ms(2026, 11, 1 + n % 10)]
    return repo


def test_growth_counts_new_materials_per_calendar_month(repo, tmp_path):
    result = query(repo, tmp_path, growth={"months": 4, "stock": True})
    total = len(repo.docs)
    sixth = [sum(1 for n in range(total) if n % 6 == k) for k in range(6)]
    assert [c["id"] for c in result["columns"]] == ["new", "stock"]
    assert [r["key"] for r in result["rows"]] == ["2026-08", "2026-09", "2026-10", "2026-11"]
    assert result["rows"][-1]["label"] == "11/2026 (bis heute)"
    assert [r["values"][0] for r in result["rows"]] == [
        sixth[3], 0, sixth[1] + sixth[2], sixth[0]]
    # Bestand am Monatsende: was es heute gibt und damals schon gab
    assert [r["values"][1] for r in result["rows"]] == [
        sixth[3] + sixth[4] + sixth[5], sixth[3] + sixth[4] + sixth[5],
        total - sixth[0], total]
    assert result["base_total"] == total and result["total_row"] is False
    assert result["provenance"]["queries"] == 5  # je Monat eine und die Grundgesamtheit
    assert "growth" in {n["code"] for n in result["provenance"]["notes"]}


def test_growth_respects_filters_and_can_follow_changes(repo, tmp_path):
    scoped = query(repo, tmp_path, growth={"months": 2},
                   filters=[{"property": SRC, "values": ["bpb_spider"]}])
    own = [n for n, d in enumerate(repo.docs) if "bpb_spider" in d[SRC]]
    assert scoped["base_total"] == len(own)
    assert scoped["rows"][-1]["values"][0] == sum(1 for n in own if n % 6 == 0)
    changed = query(repo, tmp_path, growth={"months": 2, "by": "modified"})
    assert changed["columns"][0]["label"] == "zuletzt geändert"
    assert [c["id"] for c in changed["columns"]] == ["new"]
    assert changed["rows"][-1]["values"][0] == len(repo.docs)
    with pytest.raises(QueryError, match="Zeitraum"):
        query(repo, tmp_path, growth={"months": 2}, filters=[
            {"property": "virtual:time_after_created", "values": [{"since": "1y"}]}])


def test_growth_stands_alone():
    for bad in ({"growth": {"months": 0}}, {"growth": {"months": 61}},
                {"growth": {}, "rows": {"property": TAX}},
                {"growth": {}, "fill": {"fields": [{"property": TAX}]}},
                {"growth": {"by": "sonstwas"}}):
        with pytest.raises(ValidationError):
            QuerySpec(instance="t", **bad)
    assert QuerySpec(instance="t", growth={}).growth.months == 12
