import asyncio
import json

import httpx
import pytest

from wda.config import Settings
from wda.engine.engine import run_query
from wda.engine.spec import QuerySpec
from wda.instances import InstanceRegistry
from wda.services import Services
from wda.store.definitions import DefinitionStore, NotFound
from wda.store.runner import Runner
from wda.store.specs import InvalidDefinition

from .esdata import DISC, EC, DataRepo, make_docs
from .test_definitions import tile
from .test_oersi import Oersi

TAX, LVL, LIC = "ccm:taxonid", "ccm:educationalcontext", "ccm:commonlicense_key"
M = DISC + "380"
S1 = EC + "sekundarstufe_1"
QUERY = {"instance": "a", "rows": {"property": TAX}}
INSTANCES = json.dumps([
    {"id": "a", "name": "A", "base_url": "https://a.example"},
    {"id": "b", "name": "B", "base_url": "https://b.example"},
    {"id": "kaputt", "name": "Kaputt", "base_url": "https://kaputt.example"},
])


class World:
    def __init__(self, tmp_path, keep=500):
        self.a, self.b = DataRepo(), DataRepo(make_docs(150, seed=3))
        self.oersi = Oersi()
        settings = Settings(admin_password="x", data_dir=tmp_path, instances=INSTANCES,
                            runs_keep=keep, cache_ttl=0)
        self.services = Services(settings, InstanceRegistry(INSTANCES),
                                 transport=httpx.MockTransport(self.handler))
        self.store = DefinitionStore(self.services.db)
        self.runner = Runner(self.services.db, self.services, keep=keep)

    def handler(self, request):
        host = request.url.host
        if host == "oersi.org":
            return self.oersi.handler(request)
        if host == "kaputt.example":
            return httpx.Response(500, text="x")
        return (self.a if host == "a.example" else self.b).handler(request)

    def go(self, coro):
        async def wrapped():
            try:
                return await coro
            finally:
                # der HTTP-Client hängt an der Ereignisschleife dieses Aufrufs
                if self.services._http is not None:
                    await self.services._http.aclose()
                    self.services._http = None
                    self.services._clients.clear()
                    self.services._vocabs.clear()
        return asyncio.run(wrapped())

    def direct(self, **spec):
        return self.go(run_query(QuerySpec(**spec), self.services.client(spec["instance"]),
                                 self.services.vocab(spec["instance"])))


@pytest.fixture
def world(tmp_path):
    w = World(tmp_path)
    yield w
    w.services.db.close()


def values(result):
    return {r["key"]: r["values"] for r in result["rows"]}


def test_metric_run_equals_direct_query_and_is_stored(world):
    m = world.go(world.store.create("metric", "Fächer", {"query": QUERY,
                                                         "view": {"type": "bar"}}))
    run = world.go(world.runner.run(m.id))
    assert run["status"] == "ok" and run["saved"] is True
    assert (run["definition_id"], run["kind"], run["name"], run["definition_version"]) == (
        m.id, "metric", "Fächer", 1)
    assert [r["key"] for r in run["results"]] == ["main"]
    item = run["results"][0]
    assert item["error"] is None and item["view"] == {"type": "bar", "options": {}}
    assert values(item["result"]) == values(world.direct(**QUERY))
    assert item["result"]["columns"][0]["total"] == 400
    stored = world.go(world.runner.get_run(run["id"]))
    assert stored == run
    listed = world.go(world.runner.runs(m.id))
    assert [r["id"] for r in listed] == [run["id"]] and "results" not in listed[0]
    assert listed[0]["status"] == "ok"


def test_run_without_saving(world):
    m = world.go(world.store.create("metric", "M", {"query": QUERY}))
    run = world.go(world.runner.run(m.id, save=False))
    assert run["saved"] is False and run["results"][0]["result"]["rows"]
    assert world.go(world.runner.runs(m.id)) == []
    with pytest.raises(NotFound):
        world.go(world.runner.get_run(run["id"]))


def test_dashboard_tiles_stored_filters_and_instance_override(world):
    f = world.go(world.store.create("filter", "Sek I", {"filters": [
        {"property": LVL, "values": ["Sekundarstufe I"]}]}))
    m = world.go(world.store.create("metric", "Fächer", {"query": QUERY,
                                                         "view": {"type": "pie"}}))
    d = world.go(world.store.create("dashboard", "D", {"filter_ids": [f.id], "tiles": [
        tile("fach", metric_id=m.id),
        tile("fach_balken", metric_id=m.id, view={"type": "bar"}),
        tile("lizenz", query={"instance": "a", "rows": {"property": LIC}}),
    ]}))
    run = world.go(world.runner.run(d.id))
    assert run["status"] == "ok"
    assert [r["key"] for r in run["results"]] == ["fach", "fach_balken", "lizenz"]
    assert [r["view"]["type"] for r in run["results"]] == ["pie", "bar", "table"]
    sek1 = {"property": LVL, "values": ["Sekundarstufe I"]}
    want = world.direct(instance="a", filters=[sek1], rows={"property": TAX})
    assert values(run["results"][0]["result"]) == values(want)
    assert run["results"][0]["result"]["columns"][0]["total"] == world.a.brute(
        world.a.has(LVL, S1))
    assert run["results"][0]["query"]["filters"] == [sek1]
    # dasselbe Dashboard gegen eine andere Instanz
    other = world.go(world.runner.run(d.id, instance="b"))
    assert other["status"] == "ok"
    assert other["results"][2]["result"]["provenance"]["instance"]["base_url"] == (
        "https://b.example")
    assert other["results"][0]["result"]["columns"][0]["total"] == world.b.brute(
        world.b.has(LVL, S1))
    # die Instanz am Dashboard gilt für alle Kacheln, der Aufruf geht ihr vor
    world.go(world.store.update(d.id, 1, spec={**d.spec, "instance": "b"}))
    assert world.go(world.runner.run(d.id))["results"][2]["query"]["instance"] == "b"
    assert world.go(world.runner.run(d.id, instance="a"))["results"][2]["query"][
        "instance"] == "a"


def test_filter_collisions_and_wrong_instance_fail_only_that_tile(world):
    sek1 = world.go(world.store.create("filter", "Sek I", {"filters": [
        {"property": LVL, "values": ["Sekundarstufe I"]}]}))
    only_b = world.go(world.store.create("filter", "Nur B", {"instance": "b", "filters": [
        {"property": TAX, "values": ["Mathematik"]}]}))
    d = world.go(world.store.create("dashboard", "D", {"filter_ids": [sek1.id], "tiles": [
        tile("ok", query=QUERY),
        tile("doppelt", query={"instance": "a", "filters": [
            {"property": LVL, "values": ["Hochschule"]}]}),
        tile("zeilenfeld", query={"instance": "a", "rows": {"property": LVL, "groups": [
            {"label": "X", "values": ["Hochschule"]}]}}),
    ]}))
    run = world.go(world.runner.run(d.id))
    assert run["status"] == "partial"
    by_key = {r["key"]: r for r in run["results"]}
    assert by_key["ok"]["error"] is None
    assert by_key["doppelt"]["result"] is None
    assert by_key["doppelt"]["error"]["kind"] == "invalid_query"
    assert "Sek I" in by_key["doppelt"]["error"]["detail"]
    assert LVL in by_key["doppelt"]["error"]["detail"]
    assert by_key["zeilenfeld"]["error"]["kind"] == "invalid_query"
    m = world.go(world.store.create("metric", "M", {"query": QUERY,
                                                    "filter_ids": [only_b.id]}))
    run = world.go(world.runner.run(m.id))
    assert run["status"] == "failed"
    assert "gilt nur für die Instanz 'b'" in run["results"][0]["error"]["detail"]
    assert world.go(world.runner.run(m.id, instance="b"))["status"] == "ok"


def test_partial_failures_keep_the_good_tables(world):
    d = world.go(world.store.create("dashboard", "D", {"tiles": [
        tile("gut", query=QUERY),
        tile("repo", query={"instance": "kaputt"}),
        tile("label", query={"instance": "a", "filters": [
            {"property": TAX, "values": ["Mathematk"]}]}),
        tile("instanz", query={"instance": "gibt-es-nicht"}),
    ]}))
    run = world.go(world.runner.run(d.id))
    assert run["status"] == "partial"
    by_key = {r["key"]: r for r in run["results"]}
    assert by_key["gut"]["result"]["rows"] and by_key["gut"]["error"] is None
    assert by_key["repo"]["error"]["kind"] == "unavailable"
    assert by_key["label"]["error"]["kind"] == "unresolved_value"
    assert by_key["label"]["error"]["suggestions"][0]["caption"] == "Mathematik"
    assert by_key["instanz"]["error"]["kind"] == "unknown_instance"
    assert world.go(world.runner.get_run(run["id"])) == run
    all_bad = world.go(world.store.create("metric", "M", {"query": {"instance": "kaputt"}}))
    assert world.go(world.runner.run(all_bad.id))["status"] == "failed"
    assert world.go(world.runner.runs(all_bad.id))[0]["status"] == "failed"


def test_template_runs_data_sections_only(world):
    m = world.go(world.store.create("metric", "M", {"query": QUERY}))
    t = world.go(world.store.create("template", "T", {"title": "Bericht", "sections": [
        {"id": "intro", "type": "text", "markdown": "# Hallo"},
        {"id": "tab", "type": "table", "title": "Tabelle", "metric_id": m.id},
        {"id": "bild", "type": "chart", "title": "Grafik",
         "query": {"instance": "a", "rows": {"property": LIC}}, "view": {"type": "pie"}},
    ]}))
    run = world.go(world.runner.run(t.id))
    assert [(r["key"], r["title"]) for r in run["results"]] == [
        ("tab", "Tabelle"), ("bild", "Grafik")]
    assert run["spec"]["sections"][0]["markdown"] == "# Hallo"
    empty = world.go(world.store.create("dashboard", "Leer", {"tiles": []}))
    assert world.go(world.runner.run(empty.id))["status"] == "ok"


def test_filters_cannot_be_run_and_unknown_ids(world):
    f = world.go(world.store.create("filter", "F", {"filters": [
        {"property": TAX, "values": ["Mathematik"]}]}))
    with pytest.raises(InvalidDefinition, match="Filter"):
        world.go(world.runner.run(f.id))
    with pytest.raises(NotFound):
        world.go(world.runner.run("gibt-es-nicht"))
    with pytest.raises(NotFound):
        world.go(world.runner.get_run("gibt-es-nicht"))
    with pytest.raises(NotFound):
        world.go(world.runner.delete_run("gibt-es-nicht"))


def test_run_survives_changes_and_deletion_of_the_definition(world):
    m = world.go(world.store.create("metric", "Alt", {"query": QUERY}))
    run = world.go(world.runner.run(m.id))
    world.go(world.store.update(m.id, 1, name="Neu", spec={
        "query": {"instance": "a", "rows": {"property": LIC}}}))
    kept = world.go(world.runner.get_run(run["id"]))
    assert kept["name"] == "Alt" and kept["spec"]["query"]["rows"]["property"] == TAX
    world.go(world.store.delete(m.id))
    kept = world.go(world.runner.get_run(run["id"]))
    assert kept["definition_id"] is None and kept["results"][0]["result"]["rows"]
    assert [r["id"] for r in world.go(world.runner.runs())] == [run["id"]]
    world.go(world.runner.delete_run(run["id"]))
    assert world.go(world.runner.runs()) == []


def test_retention_per_definition_and_paging(tmp_path):
    world = World(tmp_path, keep=3)
    m = world.go(world.store.create("metric", "M", {"query": {"instance": "a"}}))
    other = world.go(world.store.create("metric", "O", {"query": {"instance": "a"}}))
    first_other = world.go(world.runner.run(other.id))
    ids = [world.go(world.runner.run(m.id))["id"] for _ in range(5)]
    listed = world.go(world.runner.runs(m.id))
    assert [r["id"] for r in listed] == ids[:1:-1]  # die drei neuesten, neueste zuerst
    assert [r["id"] for r in world.go(world.runner.runs(other.id))] == [first_other["id"]]
    page = world.go(world.runner.runs(m.id, limit=2))
    assert [r["id"] for r in page] == [ids[4], ids[3]]
    rest = world.go(world.runner.runs(m.id, limit=2, before=page[-1]["seq"]))
    assert [r["id"] for r in rest] == [ids[2]]
    world.services.db.close()


def test_refresh_bypasses_the_cache(tmp_path):
    world = World(tmp_path)
    world.services.cache._ttl = 900
    m = world.go(world.store.create("metric", "M", {"query": {"instance": "a"}}))

    async def three():
        one = await world.runner.run(m.id, save=False)
        two = await world.runner.run(m.id, save=False)
        three = await world.runner.run(m.id, save=False, refresh=True)
        return [r["results"][0]["result"]["provenance"]["cached"] for r in (one, two, three)]

    assert world.go(three()) == [False, True, False]
    world.services.db.close()


def test_unexpected_error_in_one_tile_keeps_the_run(world, monkeypatch):
    import wda.store.runner as runner_module
    real = runner_module.run_query

    async def flaky(spec, client, vocab):
        if spec.rows is None:
            raise RuntimeError("streng-geheim: Programmfehler")
        return await real(spec, client, vocab)

    monkeypatch.setattr(runner_module, "run_query", flaky)
    d = world.go(world.store.create("dashboard", "D", {"tiles": [
        tile("gut", query=QUERY), tile("kaputt", query={"instance": "a"})]}))
    run = world.go(world.runner.run(d.id))
    assert run["status"] == "partial"
    assert run["results"][1]["error"] == {
        "detail": "Unerwarteter Fehler bei dieser Abfrage.", "kind": "internal"}
    assert world.go(world.runner.get_run(run["id"])) == run


def test_seq_is_never_reused(world):
    m = world.go(world.store.create("metric", "M", {"query": {"instance": "a"}}))
    ids = [world.go(world.runner.run(m.id))["id"] for _ in range(3)]
    seqs = [r["seq"] for r in world.go(world.runner.runs(m.id))]
    world.go(world.runner.delete_run(ids[-1]))
    world.go(world.runner.run(m.id))
    assert world.go(world.runner.runs(m.id))[0]["seq"] > max(seqs)
    with pytest.raises(InvalidDefinition, match="Instanz"):
        world.go(world.runner.run(m.id, instance="tippfehler"))
