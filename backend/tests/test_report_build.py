import pytest

from wda.report.build import build_report, format_number, format_time
from wda.report.model import Failure, Table, Text

from .test_definitions import tile
from .test_runner import LIC, QUERY, TAX, M, World


@pytest.fixture
def world(tmp_path):
    w = World(tmp_path)
    yield w
    w.services.db.close()


@pytest.mark.parametrize(("value", "text"), [
    (0, "0"), (1234567, "1.234.567"), (-1234, "-1.234"), (12.5, "12,50"), (1234.5678, "1.234,57"),
    (3.0, "3"), (None, "–"), (True, "–"), ("12", "–"), (float("nan"), "nan"),
])
def test_format_number(value, text):
    assert format_number(value) == text


def test_format_time():
    assert format_time("2026-11-13T19:50:15.857Z") == "13.11.2026 19:50 UTC"
    assert format_time(0) == "01.01.1970 00:00 UTC"
    assert format_time("unsinn") == "–" and format_time(None) == "–"


def test_metric_becomes_one_table_with_true_total(world):
    m = world.go(world.store.create("metric", "Fächer", {
        "query": {**QUERY, "percent": "column"}, "view": {"type": "bar"}}))
    run = world.go(world.runner.run(m.id))
    report = build_report(run)
    assert (report.title, report.kind, report.status) == ("Fächer", "metric", "ok")
    [table] = report.blocks
    assert isinstance(table, Table) and table.view["type"] == "bar"
    assert [c.label for c in table.columns] == ["Anzahl"]
    total = table.rows[-1]
    assert (total.kind, total.label, total.values) == ("total", "Gesamt", [400])
    math = next(r for r in table.rows if r.key == M)
    assert math.values == [world.a.brute(world.a.has(TAX, M))] and math.label == "Mathematik"
    assert math.percent[0] == pytest.approx(math.values[0] / 400 * 100, abs=0.01)
    # mehrwertig: Zeilen summieren sich über die Gesamtzahl hinaus, Gesamt bleibt die Zählung
    assert sum(r.values[0] for r in table.rows[:-1]) > 400
    [source] = table.sources
    assert source["label"] == "a" and source["base_url"] == "https://a.example"
    assert any(n["code"] == "rows_overlap" for n in table.notes)


def test_dashboard_keeps_failures_next_to_tables(world):
    d = world.go(world.store.create("dashboard", "Überblick", {"tiles": [
        tile("gut", query=QUERY),
        tile("kaputt", query={"instance": "kaputt"}),
        tile("lizenz", query={"instance": "a", "rows": {"property": LIC}})]}))
    report = build_report(world.go(world.runner.run(d.id)))
    assert [type(b) for b in report.blocks] == [Table, Failure, Table]
    assert report.blocks[1].kind == "unavailable" and report.blocks[1].title == "Kachel"
    assert report.status == "partial" and len(report.tables) == 2


def test_template_text_and_placeholders(world):
    m = world.go(world.store.create("metric", "M", {"query": QUERY}))
    t = world.go(world.store.create("template", "Vorlage", {"title": "Bericht 2026", "sections": [
        {"id": "intro", "type": "text", "markdown": (
            "Stand {{run.date}} auf {{ run.instance }}: {{tab.total.anzahl}} Materialien, "
            f"davon {{{{tab.cell.{M}.anzahl}}}} in Mathematik. "
            "{{tab.total.gibtesnicht}} {{unbekannt}} {{ 1+1 }} {{__import__('os')}}")},
        {"id": "tab", "type": "table", "title": "Fächer", "metric_id": m.id},
        {"id": "ende", "type": "text", "markdown": "Ohne Platzhalter."}]}))
    run = world.go(world.runner.run(t.id))
    report = build_report(run)
    assert report.title == "Bericht 2026"
    assert [type(b) for b in report.blocks] == [Text, Table, Text]
    text = report.blocks[0].markdown
    assert "400 Materialien" in text and "auf a:" in text and "UTC" in text
    assert f"davon {world.a.brute(world.a.has(TAX, M))} in Mathematik" in text
    for kept in ("{{tab.total.gibtesnicht}}", "{{unbekannt}}", "{{ 1+1 }}",
                 "{{__import__('os')}}"):
        assert kept in text
    assert [n["code"] for n in report.notes] == ["placeholder_unknown"] * 4


def test_reload_from_store_gives_the_same_report(world):
    m = world.go(world.store.create("metric", "M", {"query": QUERY}))
    run = world.go(world.runner.run(m.id))
    assert build_report(world.go(world.runner.get_run(run["id"]))) == build_report(run)


def test_other_schema_version_is_named_not_guessed(world):
    m = world.go(world.store.create("metric", "M", {"query": QUERY}))
    run = world.go(world.runner.run(m.id))
    run["results"][0]["result"]["schema_version"] = 99
    [block] = build_report(run).blocks
    assert isinstance(block, Failure) and block.kind == "unsupported_result"
    del run["results"][0]["result"]["schema_version"]
    assert isinstance(build_report(run).blocks[0], Failure)
