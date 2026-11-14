"""Von der Zelle zur Abfrage: Jede Tabelle sagt, mit welchen Filtern ihre Zellen gezählt sind.
Die Oberfläche öffnet damit den Explorer; Datensätze verlassen die App weiterhin nicht."""

import pytest
from fastapi.testclient import TestClient

from wda.main import create_app

from .conftest import ADMIN_PASSWORD
from .esdata import DISC, EC, DataRepo
from .test_engine import LIC, LVL, TAX


@pytest.fixture
def api(settings):
    with TestClient(create_app(settings, transport=DataRepo().transport())) as client:
        assert client.post("/api/auth/login",
                           json={"password": ADMIN_PASSWORD}).status_code == 204
        yield client


def table(api, query):
    out = api.post("/api/preview", json={"kind": "metric", "name": "M", "spec": {
        "query": {"instance": "wlo-prod", **query}}})
    assert out.status_code == 200, out.text
    [block] = out.json()["report"]["blocks"]
    assert block["type"] == "table", block
    return block


def count(api, filters):
    out = api.post("/api/query", json={"instance": "wlo-prod", "filters": filters})
    assert out.status_code == 200, out.text
    return out.json()["rows"][0]["values"][0] if out.json()["rows"] else out.json()[
        "columns"][0]["total"]


def cell_filters(drill, row, column):
    """So setzt die Oberfläche die Filter einer Zelle zusammen."""
    merged = {f["property"]: f for f in drill["filters"]}
    for flt in drill["columns"].get(column) or []:
        merged[flt["property"]] = flt
    if row["kind"] == "value":
        merged[drill["rows"]] = {"property": drill["rows"],
                                 "values": [{"value": row["key"], "literal": True}]}
    elif row["kind"] == "group":
        merged[drill["rows"]] = {"property": drill["rows"], "values": [
            {"value": v, "literal": True} for v in drill["groups"][row["key"]]]}
    return list(merged.values())


def test_cells_of_a_matrix_can_be_counted_again_from_their_drill(api):
    block = table(api, {"filters": [{"property": LIC, "values": ["CC_0"]}],
                        "rows": {"property": TAX, "match": DISC},
                        "columns": {"property": LVL, "match": EC}})
    drill = block["drill"]
    assert drill["instance"] == "wlo-prod" and drill["rows"] == TAX
    assert drill["filters"] == [{"property": LIC, "values": ["CC_0"]}]
    checked = 0
    for row in block["rows"][:3] + block["rows"][-1:]:
        for pos, column in enumerate(block["columns"][:2]):
            if row["values"][pos]:
                assert count(api, cell_filters(drill, row, column["id"])) == row["values"][pos]
                checked += 1
    assert checked >= 6


def test_series_and_groups_carry_their_filters_and_formulas_do_not_drill(api):
    block = table(api, {"rows": {"property": TAX, "groups": [
        {"label": "MINT", "values": [DISC + "380", DISC + "460"]},
        {"label": "Deutsch", "values": [DISC + "120"]}]},
        "series": [{"id": "gesamt", "label": "Gesamt"},
                   {"id": "frei", "label": "Frei", "filters": [
                       {"property": LIC, "values": ["CC_0"]}]},
                   {"id": "anteil", "label": "%", "formula": "round(100 * frei / gesamt, 1)"}]})
    drill = block["drill"]
    assert set(drill["columns"]) == {"gesamt", "frei"}
    mint = block["rows"][0]
    assert mint["kind"] == "group" and len(drill["groups"][mint["key"]]) == 2
    assert count(api, cell_filters(drill, mint, "frei")) == mint["values"][1]
    assert count(api, cell_filters(drill, mint, "gesamt")) == mint["values"][0]


@pytest.mark.parametrize("query", [
    {"fill": {"fields": [{"property": TAX}]}},
    {"rows": {"property": TAX, "rollup": "top"}},
    {"vocab_check": {"fields": [{"property": TAX}]}},
    {"sizes": {"property": "ccm:replicationsource"}},
])
def test_tables_whose_cells_are_no_plain_filter_have_no_drill(api, query):
    assert table(api, query).get("drill") is None


def test_no_drill_where_the_explorer_would_count_something_else(api):
    """Der Link einer Zelle gilt nur, wenn die Abfrage dahinter dasselbe zählt."""
    # Präfix-Feld: „CC_BY“ als Filter zählt auch „CC_BY_SA“
    assert table(api, {"rows": {"property": LIC}})["drill"] is None
    # Feld, das das Repositorium nur als Facette zählt
    assert table(api, {"rows": {"property": "ccm:price"}})["drill"] is None
    # Gruppen als Summe der Einzelwerte zählen Materialien mehrfach
    grouped = {"property": TAX, "group_mode": "sum", "groups": [
        {"label": "MINT", "values": [DISC + "380", DISC + "460"]}]}
    assert table(api, {"rows": grouped})["drill"] is None
    # Themen je Fach und Stufe: die Spalte trägt hier keinen Filter
    topics = {"rows": {"property": TAX, "top": 3}, "columns": {"property": LVL, "top": 2},
              "topics": {"property": "cclom:general_keyword"}}
    assert table(api, topics)["drill"] is None
    # ein Feld, das genau zählt, behält den Link
    assert table(api, {"rows": {"property": TAX}})["drill"]["rows"] == TAX
