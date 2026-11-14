"""Jeder gespeicherte Lauf schreibt seine Zahlen als schmale Zeilen: Grundlage der Historie."""

import sqlite3

from wda.store import points as points_module
from wda.store.db import SCHEMA_VERSION, Database
from wda.store.points import points_of
from wda.store.runner import backfill_points as backfill

from .test_definitions import tile
from .test_runner import LVL, QUERY, TAX, World, world  # noqa: F401


def stored(world, run_id):  # noqa: F811
    return world.go(world.services.db.read(lambda con: {
        (r["key"], r["row_key"], r["column_id"]): r["value"] for r in con.execute(
            "SELECT * FROM points WHERE run_id=?", (run_id,))}))


def test_metric_run_writes_every_cell_and_the_total(world):  # noqa: F811
    m = world.go(world.store.create("metric", "Fächer", {"query": QUERY}))
    run = world.go(world.runner.run(m.id))
    result = run["results"][0]["result"]
    column = result["columns"][0]["id"]
    points = stored(world, run["id"])
    assert points[("main", "total:__total__", column)] == result["columns"][0]["total"] == 400
    for row in result["rows"]:
        assert points[("main", f"{row['kind']}:{row['key']}", column)] == row["values"][0]
    assert len(points) == len(result["rows"]) + 1


def test_dashboard_and_cross_table(world):  # noqa: F811
    d = world.go(world.store.create("dashboard", "D", {"tiles": [
        tile("a", query=QUERY),
        tile("b", query={**QUERY, "columns": {"property": LVL, "top": 3}}),
        tile("kaputt", query={"instance": "a", "filters": [
            {"property": TAX, "values": ["gibtsnicht"]}]})]}))
    run = world.go(world.runner.run(d.id))
    points = stored(world, run["id"])
    keys = {key for key, _, _ in points}
    assert keys == {"a", "b"}  # der gescheiterte Block hat keine Punkte
    cross = run["results"][1]["result"]
    assert len({c for k, _, c in points if k == "b"}) == len(cross["columns"]) > 1


def test_run_without_saving_writes_nothing_and_delete_cascades(world):  # noqa: F811
    m = world.go(world.store.create("metric", "M", {"query": QUERY}))
    world.go(world.runner.run(m.id, save=False))
    count = lambda con: con.execute("SELECT COUNT(*) FROM points").fetchone()[0]  # noqa: E731
    assert world.go(world.services.db.read(count)) == 0
    run = world.go(world.runner.run(m.id))
    assert world.go(world.services.db.read(count)) > 0
    world.go(world.runner.delete_run(run["id"]))
    assert world.go(world.services.db.read(count)) == 0


def test_too_many_points_keep_the_run_but_no_points(world, monkeypatch):  # noqa: F811
    monkeypatch.setattr(points_module, "MAX_POINTS", 3)
    m = world.go(world.store.create("metric", "M", {"query": QUERY}))
    run = world.go(world.runner.run(m.id))
    assert world.go(world.runner.get_run(run["id"]))["status"] == "ok"
    assert stored(world, run["id"]) == {}


def test_null_stays_null_and_origin_and_instance_are_kept(world):  # noqa: F811
    m = world.go(world.store.create("metric", "M", {"query": {
        **QUERY, "series": [{"id": "n", "label": "N"},
                            {"id": "q", "label": "Quote", "formula": "n / (n - n)"}]}}))
    run = world.go(world.runner.run(m.id, instance="b", origin="schedule"))
    points = stored(world, run["id"])
    assert points[("main", "total:__total__", "q")] is None
    head = world.go(world.runner.runs(m.id))[0]
    assert (head["origin"], head["instance"]) == ("schedule", "b")
    plain = world.go(world.runner.run(m.id))
    head = world.go(world.runner.runs(m.id))[0]
    assert (head["id"], head["origin"], head["instance"]) == (plain["id"], "manual", None)


def test_points_of_matches_the_report_table():
    run = {"id": "r", "kind": "metric", "name": "M", "spec": {}, "status": "ok",
           "started_at": "2026-01-01T00:00:00+00:00", "finished_at": "x", "results": [
               {"key": "main", "title": "M", "view": {"type": "table", "options": {}},
                "query": None, "error": {"kind": "x", "detail": "y"}, "result": None}]}
    assert points_of(run) == []


V1 = """
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
INSERT INTO meta VALUES ('schema_version', '1');
CREATE TABLE groups (id TEXT PRIMARY KEY, name TEXT NOT NULL, position INTEGER NOT NULL,
    created_at TEXT NOT NULL);
CREATE TABLE definitions (id TEXT PRIMARY KEY, kind TEXT NOT NULL, name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '', group_id TEXT REFERENCES groups(id) ON DELETE SET NULL,
    spec TEXT NOT NULL, version INTEGER NOT NULL, created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL);
CREATE TABLE refs (source TEXT NOT NULL, target TEXT NOT NULL, PRIMARY KEY (source, target));
CREATE TABLE runs (seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT NOT NULL UNIQUE,
    definition_id TEXT REFERENCES definitions(id) ON DELETE SET NULL,
    kind TEXT NOT NULL, name TEXT NOT NULL, definition_version INTEGER NOT NULL,
    spec TEXT NOT NULL, status TEXT NOT NULL, started_at TEXT NOT NULL,
    finished_at TEXT NOT NULL);
CREATE TABLE run_results (run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    key TEXT NOT NULL, position INTEGER NOT NULL, title TEXT NOT NULL,
    query TEXT, result TEXT, error TEXT, PRIMARY KEY (run_id, key));
"""


def test_migration_from_version_1_adds_columns_and_backfills_points(world, tmp_path):  # noqa: F811
    m = world.go(world.store.create("metric", "M", {"query": QUERY}))
    run = world.go(world.runner.run(m.id))
    expected = stored(world, run["id"])
    rows = world.go(world.services.db.read(lambda con: (
        [tuple(r) for r in con.execute(
            "SELECT id, kind, name, definition_version, spec, status, started_at, finished_at"
            " FROM runs")],
        [tuple(r) for r in con.execute("SELECT * FROM run_results")])))

    old = tmp_path / "alt" / "store.sqlite3"
    old.parent.mkdir()
    con = sqlite3.connect(old)
    con.executescript(V1)
    con.executemany(
        "INSERT INTO runs (id, kind, name, definition_version, spec, status, started_at,"
        " finished_at) VALUES (?,?,?,?,?,?,?,?)", rows[0])
    con.executemany("INSERT INTO run_results VALUES (?,?,?,?,?,?,?)", rows[1])
    # ein beschädigter Lauf darf das Nachtragen nicht aufhalten
    con.execute("INSERT INTO runs (id, kind, name, definition_version, spec, status, started_at,"
                " finished_at) VALUES ('kaputt','metric','K',1,'{','ok','x','x')")
    con.execute("INSERT INTO run_results VALUES ('kaputt','main',0,'K','{}','[1,2','x')")
    con.commit()
    con.close()

    db = Database(old)
    assert db.read_now(lambda c: c.execute(
        "SELECT value FROM meta WHERE key='schema_version'").fetchone()[0]) == str(SCHEMA_VERSION)
    assert SCHEMA_VERSION == 2
    assert db.read_now(lambda c: c.execute("SELECT origin, instance FROM runs").fetchone()[:]
                       ) == ("manual", None)
    assert backfill(db) == 1
    got = db.read_now(lambda c: {(r["key"], r["row_key"], r["column_id"]): r["value"]
                                 for r in c.execute("SELECT * FROM points")})
    assert got == expected
    assert backfill(db) == 0  # nur einmal
    db.close()
    again = Database(old)  # zweites Öffnen ändert nichts
    assert again.read_now(lambda c: c.execute("SELECT COUNT(*) FROM points").fetchone()[0]) == len(
        expected)
    again.close()


def test_older_runs_against_another_instance_get_it_noted_once(world):  # noqa: F811
    from wda.store.runner import backfill_instances

    m = world.go(world.store.create("metric", "M", {"query": QUERY}))
    own = world.go(world.runner.run(m.id))
    other = world.go(world.runner.run(m.id))
    db = world.services.db

    def as_before(con):  # so, wie ein Lauf gegen eine andere Instanz früher gespeichert wurde
        row = con.execute("SELECT query FROM run_results WHERE run_id=?",
                          (other["id"],)).fetchone()
        con.execute("UPDATE run_results SET query=? WHERE run_id=?",
                    (row["query"].replace(f'"{QUERY["instance"]}"', '"anderswo"'), other["id"]))
        con.execute("DELETE FROM meta WHERE key='instances_backfilled'")

    db.write_now(as_before)
    assert backfill_instances(db) == 1
    got = db.read_now(lambda c: dict(c.execute("SELECT id, instance FROM runs").fetchall()))
    assert got == {own["id"]: None, other["id"]: "anderswo"}
    assert backfill_instances(db) == 0  # nur einmal


def test_runs_from_before_a_definition_moved_stay_in_its_history(world):  # noqa: F811
    """Wurde die Definition später auf eine andere Instanz umgestellt, waren die älteren
    Läufe zu ihrer Zeit die üblichen und bleiben, wie sie sind."""
    from wda.store.runner import backfill_instances

    m = world.go(world.store.create("metric", "M", {"query": QUERY}))
    world.go(world.runner.run(m.id))
    world.go(world.runner.run(m.id))
    world.go(world.store.update(m.id, None, spec={"query": {**QUERY, "instance": "b"}}))
    world.go(world.runner.run(m.id))
    db = world.services.db
    db.write_now(lambda con: con.execute("DELETE FROM meta WHERE key='instances_backfilled'"))
    assert backfill_instances(db) == 0
