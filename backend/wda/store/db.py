"""SQLite-Datei im Datenverzeichnis. Zugriffe laufen in einem Thread, Schreiber einzeln."""

from __future__ import annotations

import asyncio
import contextlib
import os
import sqlite3
import threading
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

from ..config import ConfigError

SCHEMA_VERSION = 1
T = TypeVar("T")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS groups (
    id TEXT PRIMARY KEY, name TEXT NOT NULL, position INTEGER NOT NULL,
    created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS definitions (
    id TEXT PRIMARY KEY, kind TEXT NOT NULL, name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    group_id TEXT REFERENCES groups(id) ON DELETE SET NULL,
    spec TEXT NOT NULL, version INTEGER NOT NULL,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS definitions_kind ON definitions(kind, name);
CREATE TABLE IF NOT EXISTS refs (
    source TEXT NOT NULL REFERENCES definitions(id) ON DELETE CASCADE,
    target TEXT NOT NULL REFERENCES definitions(id) ON DELETE RESTRICT,
    PRIMARY KEY (source, target));
CREATE INDEX IF NOT EXISTS refs_target ON refs(target);
CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    definition_id TEXT REFERENCES definitions(id) ON DELETE SET NULL,
    kind TEXT NOT NULL, name TEXT NOT NULL, definition_version INTEGER NOT NULL,
    spec TEXT NOT NULL, status TEXT NOT NULL,
    started_at TEXT NOT NULL, finished_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS runs_definition ON runs(definition_id, started_at);
CREATE TABLE IF NOT EXISTS run_results (
    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    key TEXT NOT NULL, position INTEGER NOT NULL, title TEXT NOT NULL,
    query TEXT, result TEXT, error TEXT,
    PRIMARY KEY (run_id, key));
"""


class Database:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()  # eine Verbindung, ein Zugriff zur Zeit
        self._con: sqlite3.Connection | None = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            con = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
            con.row_factory = sqlite3.Row
            con.execute("PRAGMA journal_mode=WAL")
            con.execute("PRAGMA foreign_keys=ON")
            con.execute("PRAGMA busy_timeout=5000")
            con.execute("BEGIN IMMEDIATE")
            con.executescript(_SCHEMA)  # beendet die Transaktion selbst
            row = con.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
        except (sqlite3.Error, OSError) as exc:
            raise ConfigError(
                f"Die Datenbank {self.path} lässt sich nicht öffnen ({type(exc).__name__}). "
                "Datei und Rechte im Datenverzeichnis prüfen.") from None
        found = int(row["value"]) if row and str(row["value"]).isdigit() else None
        if row is None:
            con.execute("INSERT INTO meta (key, value) VALUES ('schema_version', ?)",
                        (str(SCHEMA_VERSION),))
        elif found is None or found > SCHEMA_VERSION:
            con.close()
            raise ConfigError(
                f"Die Datenbank {self.path} stammt von einer neueren Version dieser Anwendung "
                f"(Schema {row['value']}, bekannt bis {SCHEMA_VERSION}). Sie wird nicht "
                "verändert; bitte die Anwendung aktualisieren.")
        with contextlib.suppress(OSError):
            os.chmod(self.path, 0o600)
        self._con = con

    def _run(self, fn: Callable[[sqlite3.Connection], T], write: bool) -> T:
        with self._lock:
            con = self._con
            if con is None:
                raise RuntimeError("Die Datenbank ist geschlossen.")
            con.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            try:
                result = fn(con)
            except BaseException:
                con.execute("ROLLBACK")
                raise
            con.execute("COMMIT")
            return result

    async def read(self, fn: Callable[[sqlite3.Connection], T]) -> T:
        return await asyncio.to_thread(self._run, fn, False)

    async def write(self, fn: Callable[[sqlite3.Connection], T]) -> T:
        """``fn`` läuft in einer Transaktion; eine Ausnahme macht alles rückgängig."""
        return await asyncio.to_thread(self._run, fn, True)

    def close(self) -> None:
        with self._lock:
            if self._con is not None:
                self._con.close()
                self._con = None
