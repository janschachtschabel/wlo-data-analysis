"""Zeit relativ zu heute als Filterwert: Zufluss und Pflege eines Bestands zählen."""

from datetime import UTC, date, datetime

import pytest
from fastapi.testclient import TestClient

import wda.filters
from wda.main import create_app

from .conftest import ADMIN_PASSWORD
from .esdata import DataRepo

CREATED = "virtual:time_after_created"


@pytest.fixture
def repo():
    return DataRepo()


@pytest.fixture
def api(settings, repo, monkeypatch):
    # „heute“ liegt 10 Tage nach dem ersten Material des gespielten Bestands
    start = datetime.fromtimestamp(repo.T0 / 1000, UTC).date()
    monkeypatch.setattr(wda.filters, "_today", lambda: date.fromordinal(start.toordinal() + 10))
    with TestClient(create_app(settings, transport=repo.transport())) as client:
        assert client.post("/api/auth/login",
                           json={"password": ADMIN_PASSWORD}).status_code == 204
        yield client


def query(api, **spec):
    r = api.post("/api/query", json={"instance": "wlo-prod", **spec})
    assert r.status_code == 200, r.text
    return r.json()


def test_since_counts_what_came_after_the_day(api, repo):
    start = datetime.fromtimestamp(repo.T0 / 1000, UTC).date()
    day = datetime(start.year, start.month, start.day, tzinfo=UTC).timestamp() * 1000
    out = query(api, series=[
        {"id": "alle", "label": "alle"},
        {"id": "neu", "label": "neu", "filters": [
            {"property": CREATED, "values": [{"since": "5d"}]}]}])
    alle, neu = (c["total"] for c in out["columns"])
    # je Stunde ein Material; fünf Tage vor „heute“ ist der sechste Tag des Bestands
    cut = day + 5 * 86_400_000
    expected = sum(1 for n, doc in enumerate(repo.docs)
                   if repo.time_of(n, doc, "cm:created") >= cut)
    assert 0 < neu < alle and neu == expected
    described = out["provenance"]["columns"][1]["filters"][0]["values"][0]
    assert described["how"] == "since" and described["label"].startswith("seit ")
    assert described["value"] == str(int(cut))


def test_since_takes_days_months_and_years(monkeypatch):
    monkeypatch.setattr(wda.filters, "_today", lambda: date(2026, 3, 31))
    assert wda.filters.since_day("30d") == date(2026, 3, 1)
    assert wda.filters.since_day("1m") == date(2026, 2, 28)  # den 31. Februar gibt es nicht
    assert wda.filters.since_day("12m") == date(2025, 3, 31)
    assert wda.filters.since_day("2y") == date(2024, 3, 31)


def test_since_only_fits_the_time_criteria(api):
    r = api.post("/api/query", json={"instance": "wlo-prod", "filters": [
        {"property": "ccm:taxonid", "values": [{"since": "5d"}]}]})
    assert r.status_code == 422 and "virtual:time_after_created" in r.json()["detail"]
    for bad in ["5", "0d", "5w", "-5d", "12345d"]:
        r = api.post("/api/query", json={"instance": "wlo-prod", "filters": [
            {"property": CREATED, "values": [{"since": bad}]}]})
        assert r.status_code == 422, bad


def test_a_hidden_series_only_feeds_formulas(api):
    series = [
        {"id": "alle", "label": "alle", "hidden": True},
        {"id": "neu", "label": "neu", "hidden": True, "filters": [
            {"property": CREATED, "values": [{"since": "5d"}]}]},
        {"id": "alt", "label": "älter", "formula": "alle - neu"}]
    out = query(api, rows={"property": "ccm:replicationsource"}, series=series)
    full = query(api, rows={"property": "ccm:replicationsource"},
                 series=[{**s, "hidden": False} for s in series])
    assert [c["id"] for c in out["columns"]] == ["alt"]
    assert [c["id"] for c in out["provenance"]["columns"]] == ["alt"]
    assert [r["values"] for r in out["rows"]] == [[r["values"][2]] for r in full["rows"]]
    assert all(len(r["values"]) == 1 for r in out["rows"])
    r = api.post("/api/query", json={"instance": "wlo-prod", "series": [
        {"id": "a", "label": "a", "hidden": True}]})
    assert r.status_code == 422


def test_the_source_streams_of_the_builtin_dashboard(api):
    assert api.post("/api/builtin/quellen/install").status_code == 200
    run = api.post("/api/definitions/builtin-quellen/run", json={"save": False}).json()
    by_key = {r["key"]: r for r in run["results"]}
    for key in ("strom-quellen", "strom-bezugsquellen", "zufluss-jahre"):
        assert by_key[key]["error"] is None, by_key[key]["error"]
    stream = by_key["strom-quellen"]["result"]
    assert [c["id"] for c in stream["columns"]] == [
        "gesamt", "neu30", "neu12", "zufluss", "ruhend", "ruhend_anteil"]
    for row in stream["rows"]:
        if row["kind"] == "value":
            total, _new30, new12, share, idle, _ = row["values"]
            assert 0 <= new12 <= total and 0 <= idle <= total
            assert share == round(new12 / total * 100, 1)
    ages = by_key["zufluss-jahre"]["result"]
    assert len(ages["columns"]) == 6
    whole = {r["key"]: r["values"][0] for r in stream["rows"]}
    for row in ages["rows"]:
        if row["kind"] == "value":
            assert sum(row["values"]) == whole[row["key"]]
    # der Kontext einer Quelle engt auch die Ströme ein
    scope = [{"property": "ccm:oeh_publisher_combined",
              "values": [{"value": ages["rows"][0]["key"], "literal": True}]}]
    scoped = api.post("/api/definitions/builtin-quellen/run",
                      json={"save": False, "scope": scope}).json()
    assert {r["key"]: r["error"] for r in scoped["results"]}["strom-quellen"] is None
