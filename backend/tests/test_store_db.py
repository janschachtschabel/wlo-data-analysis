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


def test_newer_schema_refuses_to_start(tmp_path):
    path = tmp_path / "s.sqlite3"
    db = Database(path)
    run(db.write(lambda con: con.execute(
        "UPDATE meta SET value=? WHERE key='schema_version'", (str(SCHEMA_VERSION + 1),))))
    db.close()
    with pytest.raises(ConfigError, match="neuer"):
        Database(path)


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


def test_closed_database_reopens_lazily_never_half_open(tmp_path):
    db = Database(tmp_path / "s.sqlite3")
    db.close()
    db.close()
    with pytest.raises(RuntimeError, match="geschlossen"):
        run(db.read(count("groups")))
