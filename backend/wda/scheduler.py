"""Der Planer: führt fällige Zeitpläne nacheinander aus.

Er läuft als Aufgabe im Prozess der App. Kein Fehler eines Laufs beendet ihn; wird die App
beendet, bricht der laufende Lauf ab und wird nicht gespeichert.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from .config import ConfigError
from .errors import QueryError, UnknownInstance
from .store.definitions import NotFound
from .store.runner import Runner
from .store.schedules import ScheduleStore
from .store.specs import InvalidDefinition

log = logging.getLogger(__name__)

INTERVAL = 30.0
# Fehler, die ein neuer Versuch nicht behebt: Die Definition selbst passt nicht.
_PERMANENT = (InvalidDefinition, NotFound, UnknownInstance, QueryError)


# Fehlerarten einer Abfrage, die an der Definition liegen und beim nächsten Versuch bleiben
_OWN_FAULT = frozenset({"invalid_query", "unknown_instance", "unresolved_value"})


def utc_now() -> datetime:
    return datetime.now(UTC)


def first_error(run: dict[str, Any]) -> str | None:
    for result in run.get("results", []):
        if result.get("error"):
            return str(result["error"].get("detail") or "Fehler")
    return None


class Scheduler:
    def __init__(self, schedules: Callable[[], ScheduleStore], runner: Callable[[], Runner], *,
                 clock: Callable[[], datetime] = utc_now,
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
                 also: Callable[[datetime], Awaitable[Any]] | None = None) -> None:
        self._also = also  # weitere regelmäßige Arbeit, etwa das Nachziehen der Abzüge
        self._schedules = schedules
        self._runner = runner
        self._clock = clock
        self._sleep = sleep
        self._running: set[str] = set()

    def running(self, schedule_id: str) -> bool:
        return schedule_id in self._running

    async def execute(self, schedule: dict[str, Any], *, advance: bool = True,
                      run_id: str | None = None,
                      progress: Callable[[int, int], None] | None = None
                      ) -> dict[str, Any] | None | bool:
        """Ein Lauf für einen Zeitplan; das Ergebnis steht danach am Zeitplan. Liefert den
        Lauf, ``None``, wenn er vor dem Speichern scheiterte, und ``False``, wenn nichts lief:
        Ein fälliger Termin (``advance``) wird vorher beansprucht; gelingt das nicht
        (ausgeschaltet, geändert, von anderer Seite genommen, läuft gerade), geschieht nichts."""
        if schedule["id"] in self._running:
            return False
        self._running.add(schedule["id"])
        try:
            if advance and not await self._schedules().claim(schedule, self._clock()):
                return False
            return await self._execute(schedule, advance, run_id, progress)
        finally:
            self._running.discard(schedule["id"])

    async def _execute(self, schedule: dict[str, Any], advance: bool, run_id: str | None,
                       progress: Callable[[int, int], None] | None) -> dict[str, Any] | None:
        started = self._clock()
        run: dict[str, Any] | None = None
        status, error, retry = "failed", None, True
        try:
            run = await self._runner().run(
                schedule["definition_id"], refresh=True, instance=schedule["instance"],
                origin="schedule", run_id=run_id, progress=progress)
            status = run["status"]
            error = first_error(run) if status != "ok" else None
            # Ein neuer Versuch hilft nur, wenn etwas außerhalb gestört war.
            retry = status == "failed" and any(
                (r.get("error") or {}).get("kind") not in _OWN_FAULT
                for r in run["results"] if r.get("error"))
        except _PERMANENT as exc:
            error, retry = str(exc), False
        except ConfigError as exc:
            error = str(exc)
        except Exception:
            log.exception("Zeitplan %s: unerwarteter Fehler", schedule["id"])
            error = "Unerwarteter Fehler beim Lauf; Näheres steht im Protokoll der App."
        await self._schedules().record(
            schedule, started=started, now=self._clock(), run_id=run["id"] if run else None,
            status=status, error=error, retry=retry, advance=advance)
        return run

    async def tick(self) -> int:
        """Führt alles Fällige aus, eins nach dem anderen. Liefert die Zahl der Läufe."""
        done = 0
        for schedule in await self._schedules().due(self._clock()):
            if schedule["id"] in self._running:
                continue  # läuft gerade von Hand; der Termin bleibt für den nächsten Durchgang
            try:
                done += await self.execute(schedule) is not False
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Zeitplan %s ließ sich nicht abschließen", schedule["id"])
                done += 1
        return done

    async def run_forever(self) -> None:
        while True:
            try:
                await self.tick()
                if self._also is not None:
                    await self._also(self._clock())
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Der Planer hatte einen Fehler und macht weiter")
            await self._sleep(INTERVAL)
