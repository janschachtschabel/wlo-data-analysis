"""Aufträge: Läufe, die im Hintergrund laufen, mit Fortschritt.

Der Stand steht nur im Speicher des Prozesses. Ein fertiger Lauf liegt wie jeder andere in
der Ablage; ein gescheiterter Auftrag bleibt eine Weile abrufbar und verfällt dann.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from .errors import QueryError, UnknownInstance
from .store.definitions import NotFound, new_id, now_iso
from .store.specs import InvalidDefinition

log = logging.getLogger(__name__)

MAX_RUNNING = 4
KEEP_FAILED = 600.0  # Sekunden
MAX_FAILED = 50  # so viele gescheiterte Aufträge bleiben höchstens abrufbar

Work = Callable[[str, Callable[[int, int], None]], Awaitable[Any]]


class TooManyJobs(Exception):
    def __init__(self) -> None:
        super().__init__(f"Es laufen schon {MAX_RUNNING} Aufträge. Bitte warten, bis einer "
                         "fertig ist.")


@dataclass
class Job:
    id: str
    definition_id: str
    started_at: str
    status: str = "running"
    done: int = 0
    total: int = 0
    error: dict[str, Any] | None = None
    ended: float | None = None
    task: asyncio.Task[None] | None = field(default=None, repr=False)

    def view(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "id": self.id, "definition_id": self.definition_id, "status": self.status,
            "started_at": self.started_at, "saved": False,
            "progress": {"done": self.done, "total": self.total}}
        if self.error is not None:
            out["error"] = self.error
        return out


class Jobs:
    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._jobs: dict[str, Job] = {}
        self._clock = clock

    def _expire(self) -> None:
        now = self._clock()
        for job_id in [j.id for j in self._jobs.values()
                       if j.ended is not None and now - j.ended > KEEP_FAILED]:
            del self._jobs[job_id]

    def get(self, job_id: str) -> Job | None:
        self._expire()
        return self._jobs.get(job_id)

    def start(self, definition_id: str, work: Work) -> Job:
        """``work(run_id, progress)`` führt den Lauf aus und speichert ihn unter ``run_id``."""
        self._expire()
        if sum(1 for j in self._jobs.values() if j.status == "running") >= MAX_RUNNING:
            raise TooManyJobs
        job = Job(new_id(), definition_id, now_iso())

        def progress(done: int, total: int) -> None:
            job.done, job.total = done, total

        async def go() -> None:
            try:
                await work(job.id, progress)
            except asyncio.CancelledError:
                self._jobs.pop(job.id, None)
                raise
            except (InvalidDefinition, NotFound, UnknownInstance, QueryError) as exc:
                self._fail(job, {"detail": str(exc), "kind": "invalid_definition"})
            except Exception:
                log.exception("Auftrag %s ist gescheitert", job.id)
                self._fail(job, {"detail": "Unerwarteter Fehler beim Lauf.", "kind": "internal"})
            else:
                # Der Lauf liegt jetzt in der Ablage und wird von dort gelesen.
                self._jobs.pop(job.id, None)

        self._jobs[job.id] = job
        job.task = asyncio.create_task(go())
        return job

    def _fail(self, job: Job, error: dict[str, Any]) -> None:
        job.status, job.error, job.ended = "failed", error, self._clock()
        failed = [j.id for j in self._jobs.values() if j.status == "failed"]
        for job_id in failed[:-MAX_FAILED]:
            del self._jobs[job_id]

    async def aclose(self) -> None:
        tasks = [j.task for j in self._jobs.values() if j.task is not None and not j.task.done()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._jobs.clear()
