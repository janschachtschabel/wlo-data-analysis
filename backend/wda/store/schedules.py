"""Zeitpläne: wann eine Definition von selbst läuft.

Ein Zeitplan nennt nur eine Definition, eine Instanz und einen Rhythmus. Termine werden in
der Zeitzone der App gerechnet und als UTC gespeichert.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from .db import Database
from .definitions import Conflict, NotFound, fetch, new_id
from .specs import InvalidDefinition

MAX_SCHEDULES = 50
RETRY_MINUTES = (15, 30, 60)


def iso(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def parse(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(UTC)


def _local(day: datetime, at: str, tz: ZoneInfo) -> datetime:
    """Die Uhrzeit ``at`` am Kalendertag von ``day`` als UTC. Eine Uhrzeit, die es wegen der
    Sommerzeit nicht gibt, rückt um die Lücke nach hinten; eine doppelte meint die erste."""
    hour, minute = (int(part) for part in at.split(":"))
    wall = datetime(day.year, day.month, day.day, hour, minute, tzinfo=tz)
    return wall.astimezone(UTC)


def next_due(every: str, at: str, weekday: int | None, day: int | None,
             after: datetime, tz: ZoneInfo) -> datetime:
    """Der erste Termin nach ``after`` (UTC)."""
    start = after.astimezone(tz)
    for offset in range(0, 63):
        candidate_day = start + timedelta(days=offset)
        if every == "weekly" and candidate_day.weekday() != weekday:
            continue
        if every == "monthly" and candidate_day.day != day:
            continue
        due = _local(candidate_day, at, tz)
        if due > after:
            return due
    raise ValueError("kein Termin gefunden")  # nur bei unsinnigen Angaben erreichbar


def check_rhythm(every: str, at: str, weekday: int | None, day: int | None
                 ) -> tuple[int | None, int | None]:
    """Liefert Wochentag und Tag so, wie sie zum Rhythmus gehören."""
    hour, _, minute = at.partition(":")
    if not (len(at) == 5 and hour.isdigit() and minute.isdigit()
            and int(hour) < 24 and int(minute) < 60):
        raise InvalidDefinition("Die Uhrzeit hat die Form HH:MM.")
    if every == "weekly":
        if weekday is None or not 0 <= weekday <= 6:
            raise InvalidDefinition("Ein wöchentlicher Zeitplan braucht einen Wochentag (0–6).")
        return weekday, None
    if every == "monthly":
        if day is None or not 1 <= day <= 28:
            raise InvalidDefinition(
                "Ein monatlicher Zeitplan braucht einen Tag von 1 bis 28.")
        return None, day
    if every != "daily":
        raise InvalidDefinition("Der Rhythmus ist täglich, wöchentlich oder monatlich.")
    return None, None


_SELECT = ("SELECT s.*, d.name AS definition_name, d.kind AS definition_kind"
           " FROM schedules s JOIN definitions d ON d.id = s.definition_id")


def _out(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"], "definition_id": row["definition_id"],
        "definition_name": row["definition_name"], "definition_kind": row["definition_kind"],
        "instance": row["instance"], "every": row["every"], "at": row["at"],
        "weekday": row["weekday"], "day": row["day"], "enabled": bool(row["enabled"]),
        "next_due": row["next_due"], "attempts": row["attempts"],
        "last_run_id": row["last_run_id"], "last_status": row["last_status"],
        "last_error": row["last_error"], "last_started_at": row["last_started_at"],
        "created_at": row["created_at"], "updated_at": row["updated_at"]}


def _get(con: sqlite3.Connection, schedule_id: str) -> dict[str, Any]:
    row = con.execute(_SELECT + " WHERE s.id=?", (schedule_id,)).fetchone()
    if row is None:
        raise NotFound("Den Zeitplan gibt es nicht.")
    return _out(row)


class ScheduleStore:
    def __init__(self, db: Database, tz: ZoneInfo) -> None:
        self._db = db
        self._tz = tz

    async def list(self, definition_id: str | None = None) -> list[dict[str, Any]]:
        def fn(con: sqlite3.Connection) -> list[dict[str, Any]]:
            if definition_id is None:
                rows = con.execute(_SELECT + " ORDER BY s.next_due, s.id")
            else:
                rows = con.execute(_SELECT + " WHERE s.definition_id=? ORDER BY s.next_due, s.id",
                                   (definition_id,))
            return [_out(r) for r in rows]

        return await self._db.read(fn)

    async def get(self, schedule_id: str) -> dict[str, Any]:
        return await self._db.read(lambda con: _get(con, schedule_id))

    async def create(self, *, definition_id: str, instance: str | None, every: str, at: str,
                     weekday: int | None, day: int | None, enabled: bool,
                     now: datetime) -> dict[str, Any]:
        weekday, day = check_rhythm(every, at, weekday, day)

        def fn(con: sqlite3.Connection) -> dict[str, Any]:
            definition = fetch(con, definition_id)
            if definition.kind == "filter":
                raise InvalidDefinition(
                    "Ein Filter lässt sich nicht ausführen und bekommt keinen Zeitplan.")
            if con.execute("SELECT COUNT(*) FROM schedules").fetchone()[0] >= MAX_SCHEDULES:
                raise Conflict(f"Es gibt schon {MAX_SCHEDULES} Zeitpläne; mehr sind nicht "
                               "vorgesehen.")
            if con.execute("SELECT 1 FROM schedules WHERE definition_id=? AND instance IS ?",
                           (definition_id, instance)).fetchone():
                raise Conflict("Für diese Definition und Instanz gibt es schon einen Zeitplan.")
            schedule_id, stamp = new_id(), iso(now)
            con.execute(
                "INSERT INTO schedules (id, definition_id, instance, every, at, weekday, day,"
                " enabled, next_due, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (schedule_id, definition_id, instance, every, at, weekday, day, int(enabled),
                 iso(next_due(every, at, weekday, day, now, self._tz)), stamp, stamp))
            return _get(con, schedule_id)

        return await self._db.write(fn)

    async def update(self, schedule_id: str, *, every: str, at: str, weekday: int | None,
                     day: int | None, enabled: bool, now: datetime) -> dict[str, Any]:
        """Ändert Rhythmus und Schalter; der nächste Termin wird neu gerechnet."""
        weekday, day = check_rhythm(every, at, weekday, day)

        def fn(con: sqlite3.Connection) -> dict[str, Any]:
            _get(con, schedule_id)
            con.execute(
                "UPDATE schedules SET every=?, at=?, weekday=?, day=?, enabled=?, next_due=?,"
                " attempts=0, updated_at=? WHERE id=?",
                (every, at, weekday, day, int(enabled),
                 iso(next_due(every, at, weekday, day, now, self._tz)), iso(now), schedule_id))
            return _get(con, schedule_id)

        return await self._db.write(fn)

    async def delete(self, schedule_id: str) -> None:
        def fn(con: sqlite3.Connection) -> None:
            if con.execute("DELETE FROM schedules WHERE id=?", (schedule_id,)).rowcount == 0:
                raise NotFound("Den Zeitplan gibt es nicht.")

        await self._db.write(fn)

    async def due(self, now: datetime) -> list[dict[str, Any]]:
        """Fällige Zeitpläne, der am längsten wartende zuerst."""
        return await self._db.read(lambda con: [_out(r) for r in con.execute(
            _SELECT + " WHERE s.enabled=1 AND s.next_due<=? ORDER BY s.next_due, s.id",
            (iso(now),))])

    async def record(self, schedule: dict[str, Any], *, started: datetime, now: datetime,
                     run_id: str | None, status: str, error: str | None,
                     retry: bool, advance: bool = True) -> None:
        """Hält das Ergebnis eines Laufs fest. ``retry``: bald noch einmal versuchen (höchstens
        so oft, wie ``RETRY_MINUTES`` Einträge hat). ``advance=False`` lässt den Termin stehen
        (Lauf von Hand). Ist der Zeitplan inzwischen gelöscht, geschieht nichts."""
        def fn(con: sqlite3.Connection) -> None:
            row = con.execute("SELECT * FROM schedules WHERE id=?", (schedule["id"],)).fetchone()
            if row is None:
                return
            attempts, due = row["attempts"], row["next_due"]
            if advance:
                if retry and attempts < len(RETRY_MINUTES):
                    due = iso(now + timedelta(minutes=RETRY_MINUTES[attempts]))
                    attempts += 1
                else:
                    attempts = 0
                    due = iso(next_due(row["every"], row["at"], row["weekday"], row["day"],
                                       now, self._tz))
            con.execute(
                "UPDATE schedules SET next_due=?, attempts=?, last_run_id=?, last_status=?,"
                " last_error=?, last_started_at=? WHERE id=?",
                (due, attempts, run_id, status, error[:500] if error else None, iso(started),
                 schedule["id"]))

        await self._db.write(fn)
