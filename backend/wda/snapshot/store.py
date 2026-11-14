"""Der Abzug einer Instanz als eigene SQLite-Datei im Datenverzeichnis (nur für den
Dienst lesbar). Enthält Merkmale, keine Inhalte."""

from __future__ import annotations

import contextlib
import os
import sqlite3
import threading
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any, TypeVar

from .features import Features

T = TypeVar("T")
VERSION = 1
_SCHEMA = """
CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS nodes (
    id TEXT PRIMARY KEY, pass INTEGER NOT NULL, created INTEGER, modified INTEGER,
    title_len INTEGER NOT NULL, desc_len INTEGER NOT NULL, n_keywords INTEGER NOT NULL,
    has_author INTEGER NOT NULL, has_license INTEGER NOT NULL, n_subjects INTEGER NOT NULL,
    n_levels INTEGER NOT NULL, n_types INTEGER NOT NULL, n_languages INTEGER NOT NULL,
    n_roles INTEGER NOT NULL, url_key TEXT, url_host TEXT, url_https INTEGER,
    title_key TEXT, title_is_desc INTEGER NOT NULL) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS nodes_url ON nodes(url_key);
CREATE INDEX IF NOT EXISTS nodes_title ON nodes(title_key);
CREATE TABLE IF NOT EXISTS memberships (
    node TEXT NOT NULL, kind TEXT NOT NULL, name TEXT NOT NULL,
    PRIMARY KEY (node, kind, name)) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS memberships_name ON memberships(kind, name);
"""
_COLUMNS = ("created", "modified", "title_len", "desc_len", "n_keywords", "has_author",
            "has_license", "n_subjects", "n_levels", "n_types", "n_languages", "n_roles",
            "url_key", "url_host", "url_https", "title_key", "title_is_desc")


class Snapshot:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()
        self._read_lock = threading.Lock()
        self._reader: sqlite3.Connection | None = None
        self.busy = False  # ein Abzug schreibt gerade (nur im Speicher)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with contextlib.suppress(OSError):
            os.chmod(self.path.parent, 0o700)
        if not self.path.exists():
            os.close(os.open(self.path, os.O_CREAT | os.O_WRONLY, 0o600))
        con = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA synchronous=NORMAL")
        con.executescript(_SCHEMA)
        con.execute("INSERT OR IGNORE INTO state (key, value) VALUES ('version', ?)",
                    (str(VERSION),))
        with contextlib.suppress(OSError):
            os.chmod(self.path, 0o600)
        self._con: sqlite3.Connection | None = con

    def run(self, fn: Callable[[sqlite3.Connection], T], write: bool = False) -> T:
        with self._lock:
            con = self._con
            if con is None:
                raise RuntimeError("Der Abzug ist geschlossen.")
            con.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            try:
                result = fn(con)
                con.execute("COMMIT")
            except BaseException:
                if con.in_transaction:
                    with contextlib.suppress(sqlite3.Error):
                        con.execute("ROLLBACK")
                raise
            return result

    def read(self, fn: Callable[[sqlite3.Connection], T]) -> T:
        """Für Auswertungen: eigene, nur lesende Verbindung. Eine lange Zählung hält so
        weder den Abzug noch die Abfrage des Stands auf (WAL)."""
        with self._read_lock:
            if self._con is None:
                raise RuntimeError("Der Abzug ist geschlossen.")
            if self._reader is None:
                reader = sqlite3.connect(self.path, check_same_thread=False,
                                         isolation_level=None)
                reader.row_factory = sqlite3.Row
                reader.execute("PRAGMA query_only=ON")
                reader.execute("PRAGMA busy_timeout=5000")
                self._reader = reader
            con = self._reader
            con.execute("BEGIN")
            try:
                return fn(con)
            finally:
                if con.in_transaction:
                    with contextlib.suppress(sqlite3.Error):
                        con.execute("ROLLBACK")

    # ---- Zustand ---------------------------------------------------------------------
    def state(self) -> dict[str, str]:
        return self.run(lambda con: {r["key"]: r["value"]
                                     for r in con.execute("SELECT key, value FROM state")})

    def set_state(self, **values: Any) -> None:
        def fn(con: sqlite3.Connection) -> None:
            for key, value in values.items():
                if value is None:
                    con.execute("DELETE FROM state WHERE key = ?", (key,))
                else:
                    con.execute(
                        "INSERT INTO state (key, value) VALUES (?, ?) "
                        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                        (key, str(value)))
        self.run(fn, write=True)

    # ---- Schreiben -------------------------------------------------------------------
    def put(self, items: Iterable[Features], pass_no: int) -> int:
        """Legt Merkmale ab (ersetzt vorhandene) und merkt den Durchlauf, der sie sah."""
        items = list(items)

        def fn(con: sqlite3.Connection) -> None:
            con.executemany(
                f"INSERT OR REPLACE INTO nodes (id, pass, {', '.join(_COLUMNS)}) "  # noqa: S608
                f"VALUES (?, ?, {', '.join('?' * len(_COLUMNS))})",
                [(f.id, pass_no, *[getattr(f, c) for c in _COLUMNS]) for f in items])
            con.executemany("DELETE FROM memberships WHERE node = ?", [(f.id,) for f in items])
            con.executemany(
                "INSERT OR IGNORE INTO memberships (node, kind, name) VALUES (?, ?, ?)",
                [(f.id, kind, name) for f in items
                 for kind, names in (("source", f.sources), ("publisher", f.publishers))
                 for name in names])
        self.run(fn, write=True)
        return len(items)

    def finish_pass(self, pass_no: int) -> int:
        """Nach einem vollständigen Durchlauf: Was er nicht gesehen hat, gibt es nicht mehr."""
        def fn(con: sqlite3.Connection) -> int:
            gone = con.execute("DELETE FROM nodes WHERE pass < ?", (pass_no,)).rowcount
            con.execute("DELETE FROM memberships WHERE node NOT IN (SELECT id FROM nodes)")
            return gone
        return self.run(fn, write=True)

    def seen(self, pass_no: int) -> int:
        return self.run(lambda con: con.execute(
            "SELECT COUNT(*) FROM nodes WHERE pass >= ?", (pass_no,)).fetchone()[0])

    def count(self) -> int:
        return self.run(lambda con: con.execute("SELECT COUNT(*) FROM nodes").fetchone()[0])

    def clear(self) -> None:
        def fn(con: sqlite3.Connection) -> None:
            con.execute("DELETE FROM nodes")
            con.execute("DELETE FROM memberships")
            con.execute("DELETE FROM state WHERE key != 'version'")
        self.run(fn, write=True)

    def close(self) -> None:
        with self._read_lock:
            if self._reader is not None:
                self._reader.close()
                self._reader = None
        with self._lock:
            if self._con is not None:
                self._con.close()
                self._con = None
