import asyncio
import sqlite3

import pytest

from wda.config import ConfigError
from wda.store.db import SCHEMA_VERSION, Database


def run(coro):
    return asyncio.run(coro)


def add_group(gid, name="G"):
    def fn(con):
        con.execute("INSERT INTO groups (id, name, position, created_at) VALUES (?, ?, 0, 'x')",
                    (gid, name))
    return fn


def count(table):
    return lambda con: con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]  # noqa: S608


def test_schema_is_created_and_data_survives_restart(tmp_path):
    path = tmp_path / "sub" / "store.sqlite3"
    db = Database(path)
    run(db.write(add_group("g1")))
    tables = run(db.read(lambda con: {r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}))
    assert {"groups", "definitions", "runs", "run_results", "meta"} <= tables
    db.close()
    db = Database(path)
    assert run(db.read(count("groups"))) == 1
    assert run(db.read(lambda con: con.execute(
        "SELECT value FROM meta WHERE key='schema_version'").fetchone()[0])) == str(SCHEMA_VERSION)
    db.close()


def test_foreign_keys_and_rollback(tmp_path):
    db = Database(tmp_path / "s.sqlite3")

    def bad_definition(con):
        con.execute(
            "INSERT INTO definitions (id, kind, name, description, group_id, spec, version,"
            " created_at, updated_at) VALUES ('d1','filter','n','', 'fehlt', '{}', 1, 'x', 'x')")

    with pytest.raises(sqlite3.IntegrityError):
        run(db.write(bad_definition))

    def half(con):
        add_group("g1")(con)
        raise RuntimeError("mittendrin")

    with pytest.raises(RuntimeError):
        run(db.write(half))
    assert run(db.read(count("groups"))) == 0
    db.close()


def test_rows_are_addressable_by_name(tmp_path):
    db = Database(tmp_path / "s.sqlite3")
    run(db.write(add_group("g1", "Berichte")))
    row = run(db.read(lambda con: con.execute("SELECT * FROM groups").fetchone()))
    assert row["name"] == "Berichte"
    db.close()


def test_newer_schema_refuses_to_start_and_leaves_the_file_alone(tmp_path):
    path = tmp_path / "s.sqlite3"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    con.execute("INSERT INTO meta VALUES ('schema_version', ?)", (str(SCHEMA_VERSION + 1),))
    con.execute("CREATE TABLE definitions_v2 (id TEXT)")
    con.commit()
    con.close()

    def state():
        con = sqlite3.connect(path)
        try:
            return (sorted(r[0] for r in con.execute("SELECT name FROM sqlite_master")),
                    con.execute("PRAGMA journal_mode").fetchone()[0])
        finally:
            con.close()

    before = state()
    for _ in range(2):
        with pytest.raises(ConfigError, match="neuer"):
            Database(path)
    assert state() == before and before[1] == "delete"
    assert not path.with_name(path.name + "-wal").exists()


def test_failed_commit_does_not_block_the_connection(tmp_path):
    db = Database(tmp_path / "s.sqlite3")

    def deferred(con):
        con.execute("PRAGMA defer_foreign_keys=ON")
        con.execute(
            "INSERT INTO definitions (id, kind, name, description, group_id, spec, version,"
            " created_at, updated_at) VALUES ('d1','filter','n','', 'fehlt', '{}', 1, 'x', 'x')")

    with pytest.raises(sqlite3.IntegrityError):
        run(db.write(deferred))
    run(db.write(add_group("g1")))
    assert run(db.read(count("groups"))) == 1 and run(db.read(count("definitions"))) == 0
    db.close()


def test_sidecar_files_are_private(tmp_path):
    import os
    import stat
    if os.name != "posix":
        pytest.skip("Dateirechte nur unter POSIX")
    old = os.umask(0o022)
    try:
        db = Database(tmp_path / "s.sqlite3")
        run(db.write(add_group("g1")))
        for path in tmp_path.iterdir():
            assert stat.S_IMODE(path.stat().st_mode) == 0o600, path.name
        db.close()
    finally:
        os.umask(old)


def test_file_that_is_not_a_database(tmp_path):
    path = tmp_path / "s.sqlite3"
    path.write_bytes(b"das ist keine Datenbank" * 100)
    with pytest.raises(ConfigError, match="store.sqlite3|s.sqlite3"):
        Database(path)


def test_concurrent_writers_lose_nothing(tmp_path):
    db = Database(tmp_path / "s.sqlite3")

    async def many():
        await asyncio.gather(*[db.write(add_group(f"g{n}")) for n in range(40)])
        return await db.read(count("groups"))

    assert run(many()) == 40
    db.close()


def test_closed_database_refuses_further_use(tmp_path):
    db = Database(tmp_path / "s.sqlite3")
    db.close()
    db.close()
    with pytest.raises(RuntimeError, match="geschlossen"):
        run(db.read(count("groups")))
