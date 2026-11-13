"""Definitionen (Filter, Kennzahlen, Dashboards, Templates) und ihre Gruppen."""

from __future__ import annotations

import json
import re
import secrets
import sqlite3
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from .db import Database
from .specs import KIND_LABELS, KINDS, InvalidDefinition, dump_spec, parse_spec, references

ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{6,40}$")
MAX_NAME, MAX_DESCRIPTION = 200, 2000
UNSET: Any = object()


class NotFound(Exception):
    pass


class Conflict(Exception):
    def __init__(self, message: str, used_by: list[dict[str, str]] | None = None) -> None:
        super().__init__(message)
        self.used_by = used_by or []


@dataclass(frozen=True)
class Definition:
    id: str
    kind: str
    name: str
    description: str
    group_id: str | None
    spec: dict[str, Any]
    version: int
    created_at: str
    updated_at: str

    def public(self, with_spec: bool = True) -> dict[str, Any]:
        out = {"id": self.id, "kind": self.kind, "name": self.name,
               "description": self.description, "group_id": self.group_id,
               "version": self.version, "created_at": self.created_at,
               "updated_at": self.updated_at}
        if with_spec:
            out["spec"] = self.spec
        return out


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def sort_key(text: str) -> str:
    """Sortierung wie im Wörterbuch: Ä bei A, ohne Rücksicht auf Groß und Klein."""
    plain = unicodedata.normalize("NFKD", text.casefold())
    return "".join(ch for ch in plain if not unicodedata.combining(ch))


def new_id() -> str:
    return secrets.token_urlsafe(9)


def _check_text(text: str, what: str) -> None:
    """Text, den die Datenbank nicht speichern kann oder der Anzeigen stört."""
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        raise InvalidDefinition(f"{what} enthält ungültige Zeichen.") from None
    if "\x00" in text:
        raise InvalidDefinition(f"{what} enthält ungültige Zeichen.")


def clean_name(name: Any, what: str = "Der Name") -> str:
    if not isinstance(name, str) or not name.strip():
        raise InvalidDefinition(f"{what} fehlt.")
    name = " ".join(name.split())
    _check_text(name, what)
    if len(name) > MAX_NAME:
        raise InvalidDefinition(f"{what} ist zu lang (höchstens {MAX_NAME} Zeichen).")
    return name


def clean_description(text: Any) -> str:
    if text is None:
        return ""
    if not isinstance(text, str) or len(text) > MAX_DESCRIPTION:
        raise InvalidDefinition(
            f"Die Beschreibung ist zu lang (höchstens {MAX_DESCRIPTION} Zeichen).")
    _check_text(text, "Die Beschreibung")
    return text.strip()


def _row(row: sqlite3.Row) -> Definition:
    return Definition(row["id"], row["kind"], row["name"], row["description"], row["group_id"],
                      json.loads(row["spec"]), row["version"], row["created_at"],
                      row["updated_at"])


# ---- Bausteine auf einer Verbindung (für mehrere Schritte in einer Transaktion) ----------

def fetch(con: sqlite3.Connection, def_id: str) -> Definition:
    row = con.execute("SELECT * FROM definitions WHERE id=?", (def_id,)).fetchone()
    if row is None:
        raise NotFound(f"Die Definition '{str(def_id)[:40]}' gibt es nicht.")
    return _row(row)


def users_of(con: sqlite3.Connection, def_id: str) -> list[dict[str, str]]:
    rows = con.execute(
        "SELECT d.id, d.kind, d.name FROM refs r JOIN definitions d ON d.id = r.source "
        "WHERE r.target=? ORDER BY d.name", (def_id,)).fetchall()
    return [{"id": r["id"], "kind": r["kind"], "name": r["name"]} for r in rows]


def _check_group(con: sqlite3.Connection, group_id: str | None) -> None:
    if group_id is None:
        return
    if not isinstance(group_id, str) or con.execute(
            "SELECT 1 FROM groups WHERE id=?", (group_id,)).fetchone() is None:
        raise InvalidDefinition("Die angegebene Gruppe gibt es nicht.")


def _write_refs(con: sqlite3.Connection, def_id: str, wanted: dict[str, str]) -> None:
    for target, kind in wanted.items():
        row = con.execute("SELECT kind FROM definitions WHERE id=?", (target,)).fetchone()
        if row is None:
            raise InvalidDefinition(
                f"Verweis auf '{target}': Diese Definition gibt es nicht.")
        if row["kind"] != kind:
            raise InvalidDefinition(
                f"Verweis auf '{target}': erwartet wird {KIND_LABELS[kind]}, "
                f"gefunden {KIND_LABELS[row['kind']]}.")
    con.execute("DELETE FROM refs WHERE source=?", (def_id,))
    con.executemany("INSERT INTO refs (source, target) VALUES (?, ?)",
                    [(def_id, target) for target in wanted])


def insert(con: sqlite3.Connection, *, kind: str, name: Any, description: Any,
           group_id: str | None, spec: Any, def_id: str | None, now: str) -> Definition:
    if kind not in KINDS:
        raise InvalidDefinition(f"Unbekannte Art '{kind}'. Erlaubt: {', '.join(KINDS)}.")
    parsed = parse_spec(kind, spec)
    name, description = clean_name(name), clean_description(description)
    if def_id is None:
        def_id = new_id()
    elif not isinstance(def_id, str) or not ID_PATTERN.fullmatch(def_id):
        raise InvalidDefinition("Die ID hat unerlaubte Zeichen oder die falsche Länge.")
    elif con.execute("SELECT 1 FROM definitions WHERE id=?", (def_id,)).fetchone():
        raise Conflict(f"Die ID '{def_id}' ist schon vergeben.")
    _check_group(con, group_id)
    con.execute(
        "INSERT INTO definitions (id, kind, name, description, group_id, spec, version,"
        " created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)",
        (def_id, kind, name, description, group_id, dump_spec(parsed), now, now))
    _write_refs(con, def_id, references(parsed))
    return fetch(con, def_id)


def replace(con: sqlite3.Connection, def_id: str, version: int | None, *, name: Any = UNSET,
            description: Any = UNSET, group_id: Any = UNSET, spec: Any = UNSET,
            now: str) -> Definition:
    current = fetch(con, def_id)
    if version is not None and version != current.version:
        raise Conflict(
            "Die Definition wurde inzwischen geändert. Bitte neu laden und die Änderung "
            "wiederholen.")
    new_name = current.name if name is UNSET else clean_name(name)
    new_description = (current.description if description is UNSET
                       else clean_description(description))
    new_group = current.group_id if group_id is UNSET else group_id
    if group_id is not UNSET:
        _check_group(con, new_group)
    spec_text = json.dumps(current.spec, ensure_ascii=False, sort_keys=True)
    if spec is not UNSET:
        parsed = parse_spec(current.kind, spec)
        spec_text = dump_spec(parsed)
        _write_refs(con, def_id, references(parsed))
    con.execute(
        "UPDATE definitions SET name=?, description=?, group_id=?, spec=?, version=version+1,"
        " updated_at=? WHERE id=?",
        (new_name, new_description, new_group, spec_text, now, def_id))
    return fetch(con, def_id)


def remove(con: sqlite3.Connection, def_id: str) -> None:
    current = fetch(con, def_id)
    used = users_of(con, def_id)
    if used:
        names = ", ".join(u["name"] for u in used[:5]) + (" …" if len(used) > 5 else "")
        raise Conflict(
            f"'{current.name}' wird noch verwendet von: {names}. Erst dort entfernen.", used)
    con.execute("DELETE FROM definitions WHERE id=?", (def_id,))


# ---- Ablage ------------------------------------------------------------------------------

class DefinitionStore:
    def __init__(self, db: Database, clock: Callable[[], str] = now_iso) -> None:
        self._db = db
        self._clock = clock

    async def list(self, kind: str | None = None, group_id: str | None = None,
                   q: str | None = None) -> list[dict[str, Any]]:
        if kind is not None and kind not in KINDS:
            raise InvalidDefinition(f"Unbekannte Art '{kind}'. Erlaubt: {', '.join(KINDS)}.")
        where, args = [], []
        if kind:
            where.append("kind=?")
            args.append(kind)
        if group_id == "":
            where.append("group_id IS NULL")
        elif group_id:
            where.append("group_id=?")
            args.append(group_id)
        sql = ("SELECT id, kind, name, description, group_id, version, created_at, updated_at "  # noqa: S608
               "FROM definitions" + (" WHERE " + " AND ".join(where) if where else ""))
        needle = (q or "").casefold().strip()

        def fn(con: sqlite3.Connection) -> list[dict[str, Any]]:
            rows = [dict(r) for r in con.execute(sql, args)]
            if needle:  # in Python: casefold kann auch Umlaute, SQLite nicht
                rows = [r for r in rows
                        if needle in r["name"].casefold() or needle in r["description"].casefold()]
            last = {r["definition_id"]: {"id": r["id"], "status": r["status"],
                                         "started_at": r["started_at"]}
                    for r in con.execute(
                        "SELECT id, definition_id, status, started_at FROM runs WHERE seq IN ("
                        " SELECT MAX(seq) FROM runs WHERE definition_id IS NOT NULL"
                        " GROUP BY definition_id)")}
            for row in rows:
                row["last_run"] = last.get(row["id"])
            return sorted(rows, key=lambda r: (sort_key(r["name"]), r["id"]))

        return await self._db.read(fn)

    async def get(self, def_id: str) -> Definition:
        return await self._db.read(lambda con: fetch(con, def_id))

    async def create(self, kind: str, name: Any, spec: Any, *, description: Any = "",
                     group_id: str | None = None, def_id: str | None = None) -> Definition:
        return await self._db.write(lambda con: insert(
            con, kind=kind, name=name, description=description, group_id=group_id, spec=spec,
            def_id=def_id, now=self._clock()))

    async def update(self, def_id: str, version: int | None, **changes: Any) -> Definition:
        return await self._db.write(
            lambda con: replace(con, def_id, version, now=self._clock(), **changes))

    async def delete(self, def_id: str) -> None:
        await self._db.write(lambda con: remove(con, def_id))

    async def duplicate(self, def_id: str) -> Definition:
        def fn(con: sqlite3.Connection) -> Definition:
            source = fetch(con, def_id)
            name = (source.name[: MAX_NAME - 8] + " (Kopie)")
            return insert(con, kind=source.kind, name=name, description=source.description,
                          group_id=source.group_id, spec=source.spec, def_id=None,
                          now=self._clock())

        return await self._db.write(fn)

    async def used_by(self, def_id: str) -> list[dict[str, str]]:
        def fn(con: sqlite3.Connection) -> list[dict[str, str]]:
            fetch(con, def_id)
            return users_of(con, def_id)

        return await self._db.read(fn)

    # ---- Gruppen ----

    async def groups(self) -> list[dict[str, Any]]:
        def fn(con: sqlite3.Connection) -> list[dict[str, Any]]:
            return [dict(r) for r in con.execute(
                "SELECT g.id, g.name, g.position, g.created_at, COUNT(d.id) AS definitions "
                "FROM groups g LEFT JOIN definitions d ON d.group_id = g.id "
                "GROUP BY g.id ORDER BY g.position, g.name")]

        return await self._db.read(fn)

    async def create_group(self, name: Any, group_id: str | None = None) -> dict[str, Any]:
        name = clean_name(name, "Der Name der Gruppe")

        def fn(con: sqlite3.Connection) -> str:
            gid = group_id or new_id()
            if not ID_PATTERN.fullmatch(gid):
                raise InvalidDefinition("Die ID hat unerlaubte Zeichen oder die falsche Länge.")
            if con.execute("SELECT 1 FROM groups WHERE id=?", (gid,)).fetchone():
                raise Conflict(f"Die ID '{gid}' ist schon vergeben.")
            position = con.execute("SELECT COALESCE(MAX(position), -1) + 1 FROM groups"
                                   ).fetchone()[0]
            con.execute("INSERT INTO groups (id, name, position, created_at) VALUES (?,?,?,?)",
                        (gid, name, position, self._clock()))
            return gid

        gid = await self._db.write(fn)
        return next(g for g in await self.groups() if g["id"] == gid)

    async def update_group(self, group_id: str, *, name: Any = UNSET,
                           position: Any = UNSET) -> dict[str, Any]:
        if name is not UNSET:
            name = clean_name(name, "Der Name der Gruppe")
        if position is not UNSET and (isinstance(position, bool)
                                      or not isinstance(position, int) or position < 0):
            raise InvalidDefinition("Die Position muss eine Zahl ab 0 sein.")

        def fn(con: sqlite3.Connection) -> None:
            ids = [r["id"] for r in con.execute(
                "SELECT id FROM groups ORDER BY position, name")]
            if group_id not in ids:
                raise NotFound("Die Gruppe gibt es nicht.")
            if name is not UNSET:
                con.execute("UPDATE groups SET name=? WHERE id=?", (name, group_id))
            if position is not UNSET:
                ids.remove(group_id)
                ids.insert(min(position, len(ids)), group_id)
                con.executemany("UPDATE groups SET position=? WHERE id=?",
                                [(pos, gid) for pos, gid in enumerate(ids)])

        await self._db.write(fn)
        return next(g for g in await self.groups() if g["id"] == group_id)

    async def delete_group(self, group_id: str) -> None:
        """Die Definitionen der Gruppe bleiben und stehen danach ohne Gruppe da."""
        def fn(con: sqlite3.Connection) -> None:
            if con.execute("DELETE FROM groups WHERE id=?", (group_id,)).rowcount == 0:
                raise NotFound("Die Gruppe gibt es nicht.")

        await self._db.write(fn)
