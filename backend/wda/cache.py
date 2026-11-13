"""TTL-Cache für Aggregat-Antworten mit Zusammenlegen gleichzeitiger gleicher Anfragen."""

from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Hashable
from typing import Any


class TTLCache:
    def __init__(
        self,
        ttl: float,
        max_entries: int = 2000,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._ttl = ttl
        self._max_entries = max_entries
        self._clock = clock
        self._entries: OrderedDict[Hashable, tuple[float, Any]] = OrderedDict()
        self._loading: dict[Hashable, asyncio.Task[Any]] = {}

    def __len__(self) -> int:
        return len(self._entries)

    def clear(self) -> None:
        self._entries.clear()

    async def get_or_load(
        self,
        key: Hashable,
        loader: Callable[[], Awaitable[Any]],
        refresh: bool = False,
    ) -> tuple[Any, bool]:
        """Liefert ``(wert, aus_cache)``. Fehler des Laders werden nicht gecacht.

        Läuft für den Schlüssel bereits ein Ladevorgang, wird dessen Ergebnis geliefert,
        auch bei ``refresh=True``: Es ist höchstens wenige Sekunden alt.
        """
        if not refresh:
            entry = self._entries.get(key)
            if entry is not None:
                if entry[0] > self._clock():
                    return entry[1], True
                del self._entries[key]
        task = self._loading.get(key)
        if task is None:
            task = asyncio.ensure_future(self._load(key, loader))
            self._loading[key] = task
            task.add_done_callback(lambda done, key=key: self._finished(key, done))
        # shield: bricht ein Wartender ab, läuft das Laden für die übrigen weiter
        return await asyncio.shield(task), False

    async def _load(self, key: Hashable, loader: Callable[[], Awaitable[Any]]) -> Any:
        value = await loader()
        if self._ttl > 0:
            now = self._clock()
            if len(self._entries) >= self._max_entries // 2:
                for stale in [k for k, (expires, _) in self._entries.items() if expires <= now]:
                    del self._entries[stale]
            self._entries[key] = (now + self._ttl, value)
            self._entries.move_to_end(key)
            while len(self._entries) > self._max_entries:
                self._entries.popitem(last=False)
        return value

    def _finished(self, key: Hashable, task: asyncio.Task[Any]) -> None:
        if self._loading.get(key) is task:
            del self._loading[key]
        if not task.cancelled():
            task.exception()  # als abgeholt markieren, auch wenn niemand mehr wartet
