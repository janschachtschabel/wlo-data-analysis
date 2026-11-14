import asyncio

import pytest

from wda.store.db import Database
from wda.store.definitions import Conflict, DefinitionStore, NotFound
from wda.store.specs import InvalidDefinition, parse_spec

TAX = "ccm:taxonid"
FILTER = {"filters": [{"property": TAX, "values": ["Mathematik"]}]}
QUERY = {"instance": "wlo-prod", "rows": {"property": TAX}}
METRIC = {"query": QUERY}


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def store(tmp_path):
    db = Database(tmp_path / "s.sqlite3")
    yield DefinitionStore(db)
    db.close()


def tile(tid="t1", **source):
    return {"id": tid, "title": "Kachel", "layout": {"x": 0, "y": 0, "w": 6, "h": 4}, **source}


def test_create_get_list_and_defaults(store):
    f = run(store.create("filter", "  Mathe   nur ", FILTER, description=" d "))
    assert f.name == "Mathe nur" and f.description == "d" and f.version == 1
    assert f.spec == {"instance": None, "filters": [
        {"property": TAX, "values": ["Mathematik"]}]}
    m = run(store.create("metric", "Ärzte nach Fach", METRIC))
    assert m.spec["view"] == {"type": "table", "options": {}}
    assert run(store.get(m.id)) == m
    listed = run(store.list())
    assert [d["name"] for d in listed] == ["Ärzte nach Fach", "Mathe nur"]
    assert "spec" not in listed[0]
    assert [d["id"] for d in run(store.list(kind="filter"))] == [f.id]
    assert [d["id"] for d in run(store.list(q="ÄRZTE"))] == [m.id]
    assert run(store.list(q="gibt es nicht")) == []
    with pytest.raises(NotFound):
        run(store.get("unbekannt1"))
    with pytest.raises(InvalidDefinition):
        run(store.list(kind="bericht"))


def test_refresh_is_never_stored(store):
    m = run(store.create("metric", "M", {"query": {**QUERY, "refresh": True}}))
    assert m.spec["query"]["refresh"] is False


@pytest.mark.parametrize(("kind", "name", "spec", "match"), [
    ("bericht", "x", {}, "Unbekannte Art"),
    ("filter", "", FILTER, "Name fehlt"),
    ("filter", "x" * 201, FILTER, "zu lang"),
    ("filter", "x", {"filters": []}, "filters"),
    ("filter", "x", {"filters": FILTER["filters"] * 2}, "nur einmal"),
    ("filter", "x", {**FILTER, "geheim": 1}, "unbekanntes Feld"),
    ("metric", "x", {}, "query"),
    ("metric", "x", {"query": {"instance": "t", "series": [
        {"id": "a", "label": "A", "formula": "__import__('os')"}]}}, "Spalte 'A'"),
    ("metric", "x", {"query": QUERY, "view": {"type": "torte"}}, "view.type"),
    ("dashboard", "x", {"tiles": [tile()]}, "genau eines"),
    ("dashboard", "x", {"tiles": [tile(query=QUERY, metric_id="abcdef12")]}, "genau eines"),
    ("dashboard", "x", {"tiles": [tile(query=QUERY), tile(query=QUERY)]}, "eindeutig"),
    ("template", "x", {"sections": []}, "title"),
    ("template", "x", {"title": "T", "sections": [{"id": "a", "type": "bild"}]}, "sections.0"),
    ("template", "x", {"title": "T", "sections": [
        {"id": "a", "type": "text", "markdown": "x" * 20001}]}, "zu lang"),
])
def test_invalid_definitions(store, kind, name, spec, match):
    with pytest.raises(InvalidDefinition, match=match):
        run(store.create(kind, name, spec))
    assert run(store.list()) == []


def test_error_message_does_not_echo_input():
    with pytest.raises(InvalidDefinition) as info:
        parse_spec("filter", {"filters": [{"property": "a b", "values": ["streng-geheim"]}]})
    assert "streng-geheim" not in str(info.value)


def test_too_large_spec(store):
    sections = [{"id": f"s{n}", "type": "text", "markdown": "ä" * 9000} for n in range(40)]
    with pytest.raises(InvalidDefinition, match="zu groß"):
        run(store.create("template", "x", {"title": "T", "sections": sections}))


def test_references_must_exist_and_have_the_right_kind(store):
    f = run(store.create("filter", "F", FILTER))
    m = run(store.create("metric", "M", {**METRIC, "filter_ids": [f.id]}))
    with pytest.raises(InvalidDefinition, match="gibt es nicht"):
        run(store.create("metric", "M2", {**METRIC, "filter_ids": ["fehlt-fehlt"]}))
    with pytest.raises(InvalidDefinition, match="erwartet wird Filter, gefunden Kennzahl"):
        run(store.create("metric", "M2", {**METRIC, "filter_ids": [m.id]}))
    with pytest.raises(InvalidDefinition, match="erwartet wird Kennzahl"):
        run(store.create("dashboard", "D", {"tiles": [tile(metric_id=f.id)]}))
    with pytest.raises(InvalidDefinition, match="zugleich"):
        run(store.create("dashboard", "D", {"filter_ids": [f.id],
                                            "tiles": [tile(metric_id=f.id)]}))
    d = run(store.create("dashboard", "D", {"filter_ids": [f.id], "tiles": [
        tile("a", metric_id=m.id), tile("b", metric_id=m.id), tile("c", query=QUERY)]}))
    t = run(store.create("template", "T", {"title": "Bericht", "sections": [
        {"id": "intro", "type": "text", "markdown": "# Hallo"},
        {"id": "tab", "type": "table", "title": "Tabelle", "metric_id": m.id}]}))
    assert [u["id"] for u in run(store.used_by(m.id))] == [d.id, t.id]
    assert {u["name"] for u in run(store.used_by(f.id))} == {"D", "M"}
    assert len(run(store.list())) == 4  # die abgelehnten haben nichts hinterlassen


def test_delete_refuses_while_used_then_works(store):
    f = run(store.create("filter", "F", FILTER))
    m = run(store.create("metric", "M", {**METRIC, "filter_ids": [f.id]}))
    with pytest.raises(Conflict, match="wird noch verwendet von: M") as info:
        run(store.delete(f.id))
    assert info.value.used_by == [{"id": m.id, "kind": "metric", "name": "M"}]
    # Verweis lösen, dann geht es
    run(store.update(m.id, m.version, spec=METRIC))
    run(store.delete(f.id))
    run(store.delete(m.id))
    assert run(store.list()) == []
    with pytest.raises(NotFound):
        run(store.delete(m.id))


def test_update_needs_current_version_and_keeps_the_rest(store):
    g = run(store.create_group("Berichte"))
    m = run(store.create("metric", "M", METRIC, description="alt", group_id=g["id"]))
    m2 = run(store.update(m.id, 1, name="Neu"))
    assert (m2.name, m2.description, m2.group_id, m2.version) == ("Neu", "alt", g["id"], 2)
    assert m2.spec == m.spec and m2.created_at == m.created_at
    with pytest.raises(Conflict, match="inzwischen geändert"):
        run(store.update(m.id, 1, name="Zu spät"))
    assert run(store.get(m.id)).name == "Neu"
    m3 = run(store.update(m.id, 2, group_id=None, spec={"query": QUERY,
                                                      "view": {"type": "bar"}}))
    assert m3.group_id is None and m3.spec["view"]["type"] == "bar" and m3.version == 3
    with pytest.raises(InvalidDefinition):
        run(store.update(m.id, 3, spec={"filters": FILTER["filters"]}))  # Art bleibt Kennzahl
    with pytest.raises(InvalidDefinition, match="Gruppe"):
        run(store.update(m.id, 3, group_id="gibt-es-nicht"))
    assert run(store.get(m.id)).version == 3
    with pytest.raises(NotFound):
        run(store.update("unbekannt1", 1, name="x"))


def test_concurrent_updates_exactly_one_wins(store):
    m = run(store.create("metric", "M", METRIC))

    async def race():
        return await asyncio.gather(
            *[store.update(m.id, 1, name=f"N{n}") for n in range(8)], return_exceptions=True)

    results = run(race())
    assert sum(1 for r in results if isinstance(r, Conflict)) == 7
    assert run(store.get(m.id)).version == 2


def test_duplicate(store):
    f = run(store.create("filter", "F", FILTER))
    m = run(store.create("metric", "M" * 200, {**METRIC, "filter_ids": [f.id]}))
    copy = run(store.duplicate(m.id))
    assert copy.id != m.id and copy.name.endswith(" (Kopie)") and len(copy.name) == 200
    assert copy.spec == m.spec and copy.version == 1
    assert len(run(store.used_by(f.id))) == 2


def test_given_ids(store):
    f = run(store.create("filter", "F", FILTER, def_id="mein-filter"))
    assert f.id == "mein-filter"
    with pytest.raises(Conflict, match="schon vergeben"):
        run(store.create("filter", "F", FILTER, def_id="mein-filter"))
    for bad in ("kurz", "mit leer zeichen", "../../etc/passwd", "x" * 41):
        with pytest.raises(InvalidDefinition):
            run(store.create("filter", "F", FILTER, def_id=bad))


def test_groups(store):
    a = run(store.create_group(" Berichte "))
    b = run(store.create_group("Qualität"))
    c = run(store.create_group("Archiv"))
    assert [g["name"] for g in run(store.groups())] == ["Berichte", "Qualität", "Archiv"]
    assert a["position"] == 0 and a["definitions"] == 0
    m = run(store.create("metric", "M", METRIC, group_id=b["id"]))
    assert run(store.groups())[1]["definitions"] == 1
    assert [d["id"] for d in run(store.list(group_id=b["id"]))] == [m.id]
    assert run(store.list(group_id=a["id"])) == []
    run(store.update_group(c["id"], position=0, name="Ablage"))
    assert [g["name"] for g in run(store.groups())] == ["Ablage", "Berichte", "Qualität"]
    assert [g["position"] for g in run(store.groups())] == [0, 1, 2]
    run(store.update_group(c["id"], position=99))
    assert [g["name"] for g in run(store.groups())][-1] == "Ablage"
    # Löschen hängt aus, löscht nicht
    run(store.delete_group(b["id"]))
    assert run(store.get(m.id)).group_id is None
    assert [d["id"] for d in run(store.list(group_id=""))] == [m.id]
    with pytest.raises(NotFound):
        run(store.delete_group(b["id"]))
    with pytest.raises(NotFound):
        run(store.update_group(b["id"], name="x"))
    with pytest.raises(InvalidDefinition):
        run(store.create_group(""))
    with pytest.raises(InvalidDefinition):
        run(store.update_group(a["id"], position=-1))
    with pytest.raises(InvalidDefinition, match="Gruppe"):
        run(store.create("metric", "M", METRIC, group_id=b["id"]))
