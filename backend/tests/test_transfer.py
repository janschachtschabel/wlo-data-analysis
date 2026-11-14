import asyncio
import copy

import pytest

from wda.store.db import Database
from wda.store.definitions import DefinitionStore, NotFound
from wda.store.specs import InvalidDefinition
from wda.store.transfer import Transfer

from .test_definitions import FILTER, METRIC, QUERY, tile


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def db(tmp_path):
    db = Database(tmp_path / "s.sqlite3")
    yield db
    db.close()


@pytest.fixture
def filled(db):
    store = DefinitionStore(db)
    g = run(store.create_group("Berichte"))
    f = run(store.create("filter", "F", FILTER, def_id="filter-1"))
    m = run(store.create("metric", "M", {**METRIC, "filter_ids": [f.id]}, def_id="metric-1",
                         group_id=g["id"]))
    d = run(store.create("dashboard", "D", {"tiles": [tile(metric_id=m.id)]},
                         def_id="dash-001", group_id=g["id"]))
    run(store.create("metric", "Allein", METRIC, def_id="metric-2"))
    return store, g, f, m, d


def other(tmp_path):
    db = Database(tmp_path / "other.sqlite3")
    return db, DefinitionStore(db), Transfer(db)


def test_round_trip_into_empty_store(db, filled, tmp_path):
    store, g, *_ = filled
    data = run(Transfer(db).export())
    assert data["format"] == "wda-definitions" and data["version"] == 1
    assert [d["id"] for d in data["definitions"]] == ["filter-1", "metric-1", "metric-2",
                                                      "dash-001"]  # Verwiesene zuerst
    assert set(data["definitions"][0]) == {"id", "kind", "name", "description", "group_id",
                                           "spec"}
    db2, store2, transfer2 = other(tmp_path)
    report = run(transfer2.import_(data, "skip"))
    assert [r["action"] for r in report["definitions"]] == ["created"] * 4
    assert run(Transfer(db2).export())["definitions"] == data["definitions"]
    assert [x["name"] for x in run(store2.groups())] == ["Berichte"]
    assert run(store2.get("dash-001")).group_id == g["id"]
    assert [u["id"] for u in run(store2.used_by("metric-1"))] == ["dash-001"]
    db2.close()


def test_export_selection_brings_references_and_groups(db, filled):
    data = run(Transfer(db).export(["dash-001"]))
    assert [d["id"] for d in data["definitions"]] == ["filter-1", "metric-1", "dash-001"]
    assert [g["name"] for g in data["groups"]] == ["Berichte"]
    alone = run(Transfer(db).export(["metric-2"]))
    assert [d["id"] for d in alone["definitions"]] == ["metric-2"] and alone["groups"] == []
    with pytest.raises(NotFound):
        run(Transfer(db).export(["gibt-es-nicht"]))


def test_order_in_the_file_does_not_matter(db, filled, tmp_path):
    data = run(Transfer(db).export())
    data["definitions"].reverse()
    db2, store2, transfer2 = other(tmp_path)
    run(transfer2.import_(data, "skip"))
    assert len(run(store2.list())) == 4
    db2.close()


def test_modes_skip_replace_copy(db, filled):
    store, g, f, m, d = filled
    transfer = Transfer(db)
    data = run(transfer.export())
    changed = copy.deepcopy(data)
    changed["definitions"][1]["name"] = "M geändert"
    changed["groups"][0]["name"] = "Berichte neu"

    report = run(transfer.import_(changed, "skip"))
    assert {r["action"] for r in report["definitions"]} == {"skipped"}
    assert run(store.get(m.id)).name == "M" and run(store.groups())[0]["name"] == "Berichte"

    report = run(transfer.import_(changed, "replace"))
    assert {r["action"] for r in report["definitions"]} == {"replaced"}
    assert run(store.get(m.id)).name == "M geändert" and run(store.get(m.id)).version == 2
    assert run(store.groups())[0]["name"] == "Berichte neu"
    assert len(run(store.list())) == 4

    report = run(transfer.import_(data, "copy"))
    assert {r["action"] for r in report["definitions"]} == {"created"}
    assert len(run(store.list())) == 8
    new = {r["source_id"]: r["id"] for r in report["definitions"]}
    assert set(new) == {"filter-1", "metric-1", "metric-2", "dash-001"}
    assert not set(new.values()) & set(new)
    # Verweise zeigen auf die Kopien, nicht auf die Originale
    assert run(store.get(new["dash-001"])).spec["tiles"][0]["metric_id"] == new["metric-1"]
    assert run(store.get(new["metric-1"])).spec["filter_ids"] == [new["filter-1"]]
    assert [u["id"] for u in run(store.used_by("metric-1"))] == ["dash-001"]


def test_copy_keeps_references_to_definitions_outside_the_file(db, filled):
    store, *_ = filled
    transfer = Transfer(db)
    data = run(transfer.export(["dash-001"]))
    data["definitions"] = [d for d in data["definitions"] if d["kind"] == "dashboard"]
    report = run(transfer.import_(data, "copy"))
    new_id = report["definitions"][0]["id"]
    assert run(store.get(new_id)).spec["tiles"][0]["metric_id"] == "metric-1"


def test_broken_entry_changes_nothing_and_names_the_position(db, filled, tmp_path):
    data = run(Transfer(db).export())
    db2, store2, transfer2 = other(tmp_path)
    broken = copy.deepcopy(data)
    broken["definitions"][2]["spec"] = {"query": {"instance": "t", "geheim": 1}}
    with pytest.raises(InvalidDefinition, match=r"Eintrag 3 \('Allein'\).*unbekanntes Feld"):
        run(transfer2.import_(broken, "skip"))
    missing_ref = copy.deepcopy(data)
    missing_ref["definitions"] = [d for d in data["definitions"] if d["id"] != "filter-1"]
    with pytest.raises(InvalidDefinition, match=r"'M'.*filter-1.*gibt es nicht"):
        run(transfer2.import_(missing_ref, "skip"))
    twice = copy.deepcopy(data)
    twice["definitions"].append(twice["definitions"][0])
    with pytest.raises(InvalidDefinition, match="mehrfach"):
        run(transfer2.import_(twice, "skip"))
    assert run(store2.list()) == [] and run(store2.groups()) == []
    db2.close()


def test_replace_refuses_a_different_kind(db, filled):
    transfer = Transfer(db)
    data = run(transfer.export(["metric-2"]))
    data["definitions"][0].update(kind="filter", spec=FILTER)
    with pytest.raises(InvalidDefinition, match="anderen Art"):
        run(transfer.import_(data, "replace"))


@pytest.mark.parametrize("data", [
    None, [], "x", {}, {"format": "anderes", "version": 1, "definitions": []},
    {"format": "wda-definitions", "version": 2, "definitions": []},
    {"format": "wda-definitions", "version": 1, "definitions": "x"},
    {"format": "wda-definitions", "version": 1, "definitions": [1]},
    {"format": "wda-definitions", "version": 1, "definitions": [], "groups": [{"id": 5}]},
    {"format": "wda-definitions", "version": 1,
     "definitions": [{"id": "abcdef", "kind": "metric", "name": "x", "spec": {"query": QUERY}}]
     * 2001},
])
def test_foreign_or_malformed_files(db, data):
    with pytest.raises(InvalidDefinition):
        run(Transfer(db).import_(data, "skip"))


def test_unknown_mode_and_unknown_group(db, filled, tmp_path):
    data = run(Transfer(db).export(["metric-1"]))
    with pytest.raises(InvalidDefinition, match="mode"):
        run(Transfer(db).import_(data, "merge"))
    data["groups"] = []  # Gruppe fehlt in Datei und Ziel: Definition kommt ohne Gruppe an
    db2, store2, transfer2 = other(tmp_path)
    run(transfer2.import_(data, "skip"))
    assert run(store2.get("metric-1")).group_id is None
    db2.close()
