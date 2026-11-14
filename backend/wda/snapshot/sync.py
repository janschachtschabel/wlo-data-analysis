"""Zieht die Merkmale aller Materialien einer Instanz in den lokalen Abzug.

Verfahren (gegen WLO gemessen, siehe docs/edu-sharing-api-erkenntnisse.md): aufsteigend nach
Zeit sortieren, eine Seite holen, mit der Zeit des letzten Knotens als „seit“-Kriterium
fortsetzen. So spielt die Grenze von 10.000 Treffern keine Rolle. Aus jedem Knoten werden
sofort Merkmale; die Antwort des Repositoriums wird nirgends abgelegt."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

from ..edusharing.client import Criterion, EduSharingClient
from ..edusharing.errors import EduSharingError
from .features import PROPERTIES, features, millis
from .store import Snapshot

log = logging.getLogger(__name__)
PAGE = 1000
PAUSE = 0.5          # Sekunden zwischen zwei Seiten: das Repositorium schonen
MARGIN = 3600        # beim Nachziehen so viele Sekunden vor dem letzten Stand beginnen
_MODES = {"full": ("cm:created", "createdAt", "virtual:time_after_created"),
          "update": ("cm:modified", "modifiedAt", "virtual:time_after_modified")}


class SyncError(Exception):
    pass


def status(snapshot: Snapshot, running: bool) -> dict[str, Any]:
    state = snapshot.state()

    def number(key: str) -> float | None:
        try:
            return float(state[key])
        except (KeyError, ValueError):
            return None

    current = state.get("status", "none")
    if current == "running" and not running:
        current = "stopped"  # der Dienst wurde währenddessen beendet
    return {"status": current, "mode": state.get("mode"), "count": snapshot.count(),
            "total": number("total"), "done": number("done"),
            "started_at": number("started_at"), "finished_at": number("finished_at"),
            "error": state.get("error"), "complete": "finished_at" in state,
            "resumable": current in ("stopped", "failed") and "cursor" in state}


async def sync(client: EduSharingClient, snapshot: Snapshot, secret: bytes, mode: str = "full",
               *, page: int = PAGE, pause: float = PAUSE,
               sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
               clock: Callable[[], float] = time.time) -> dict[str, Any]:
    """``full``: alles neu, am Ende verschwindet, was es nicht mehr gibt; ein abgebrochener
    Lauf wird fortgesetzt. ``update``: nur seit dem letzten Stand Geändertes."""
    sort, node_key, since = _MODES[mode]
    state = snapshot.state()
    if mode == "update" and "finished_at" not in state:
        raise SyncError("Nachziehen geht erst nach einem vollständigen Abzug.")
    resume = (mode == "full" and state.get("mode") == "full" and "cursor" in state
              and state.get("status") in ("running", "stopped", "failed"))
    pass_no = int(state.get("pass", "0")) + (0 if resume or mode == "update" else 1)
    started = float(state["started_at"]) if resume else clock()
    if resume:
        cursor: int | None = int(state["cursor"])
    elif mode == "update":
        cursor = int((float(state["finished_at"]) - MARGIN) * 1000)
    else:
        cursor = None
    snapshot.set_state(status="running", mode=mode, started_at=started, error=None,
                       **({"pass": pass_no} if mode == "full" else {}),
                       **({} if resume else {"done": 0, "total": None, "cursor": None}))
    done = int(float(state.get("done", "0"))) if resume else 0
    skip = 0
    first = True
    try:
        while True:
            criteria = [Criterion("ngsearchword", ("*",))]
            if cursor is not None:
                criteria.append(Criterion(since, (str(cursor),)))
            total, nodes = await client.node_page(
                criteria, sort=sort, max_items=page, skip=skip, properties=PROPERTIES)
            items = [f for f in (features(n, secret) for n in nodes) if f is not None]
            # der Durchlauf der Knoten bleibt beim Nachziehen der des letzten ganzen Abzugs
            await asyncio.to_thread(snapshot.put, items, pass_no)
            del items
            last = next((t for n in reversed(nodes)
                         if (t := millis(n, node_key, sort)) is not None), None)
            count = len(nodes)
            del nodes
            if first and not resume:
                snapshot.set_state(total=total)
            first = False
            done += count
            if count < page:
                break
            if last is None:
                raise SyncError("Das Repositorium liefert keine Zeit, an der sich fortsetzen "
                                "ließe.")
            if cursor is not None and last <= cursor:
                # eine ganze Seite mit derselben Zeit: innerhalb dieser Zeit weiterblättern
                skip += count
                if skip + page > 10000:
                    raise SyncError("Zu viele Materialien mit derselben Zeit; der Abzug "
                                    "kommt hier nicht weiter.")
            else:
                cursor, skip = last, 0
            snapshot.set_state(cursor=cursor, done=done)
            if pause:
                await sleep(pause)
        gone = 0
        if mode == "full":
            gone = await asyncio.to_thread(snapshot.finish_pass, pass_no)
        snapshot.set_state(status="ok", finished_at=started, cursor=None, done=done)
        return {"count": snapshot.count(), "removed": gone, "fetched": done}
    except asyncio.CancelledError:
        snapshot.set_state(status="stopped")
        raise
    except (EduSharingError, SyncError) as exc:
        message = exc.message if isinstance(exc, EduSharingError) else str(exc)
        snapshot.set_state(status="failed", error=message[:300])
        raise
    except Exception:
        log.exception("Abzug gescheitert")
        snapshot.set_state(status="failed", error="Unerwarteter Fehler beim Abzug.")
        raise
