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
RETRY = (5.0, 30.0)  # Wartezeiten vor einem neuen Versuch derselben Seite
COMPLETE = 0.98      # so viel des gemeldeten Bestands muss ein voller Durchgang gesehen haben
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

    def whole(key: str) -> int | None:
        value = number(key)
        return None if value is None else int(value)

    current = state.get("status", "none")
    if current == "running" and not running:
        current = "stopped"  # der Dienst wurde währenddessen beendet
    return {"status": current, "mode": state.get("mode"), "count": snapshot.count(),
            "total": whole("total"), "done": whole("done"), "skipped": whole("skipped") or 0,
            "started_at": number("started_at"), "finished_at": number("finished_at"),
            "error": state.get("error"), "complete": "finished_at" in state,
            # fortsetzen lässt sich nur ein ganzer Durchgang; Nachziehen beginnt neu
            "resumable": (current in ("stopped", "failed") and "cursor" in state
                          and state.get("mode") == "full")}


def _set(snapshot: Snapshot, values: dict[str, Any]) -> None:
    snapshot.set_state(**values)


async def _page(client: EduSharingClient, sleep: Callable[[float], Awaitable[None]],
                **kwargs: Any) -> tuple[int, list[Any]]:
    """Eine Seite; ein einzelner Ausfall der Gegenstelle beendet nicht den ganzen Abzug."""
    for wait in (*RETRY, None):
        try:
            return await client.node_page(**kwargs)
        except EduSharingError as exc:
            # eine Ablehnung (4xx) käme beim zweiten Mal genauso
            if wait is None or exc.kind not in ("unavailable", "timeout", "throttled",
                                                "bad_response"):
                raise
            await sleep(wait)
    raise AssertionError  # pragma: no cover


async def sync(client: EduSharingClient, snapshot: Snapshot, secret: bytes, mode: str = "full",
               *, page: int = PAGE, pause: float = PAUSE,
               sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
               clock: Callable[[], float] = time.time) -> dict[str, Any]:
    """``full``: alles neu, am Ende verschwindet, was es nicht mehr gibt; ein abgebrochener
    voller Lauf wird fortgesetzt. ``update``: nur seit dem letzten Stand Geändertes."""
    sort, node_key, since = _MODES[mode]
    store = asyncio.to_thread  # die Ablage blockiert; nie auf der Ereignisschleife warten
    state = await store(snapshot.state)
    if mode == "update" and "finished_at" not in state:
        raise SyncError("Nachziehen geht erst nach einem vollständigen Abzug.")
    # ``cursor_mode`` sagt, wozu der Zeiger gehört: Ein Zeiger des Nachziehens (Zeit der
    # Änderung) taugt nicht zum Fortsetzen eines vollen Laufs (Zeit der Erfassung).
    # (Ältere Stände kennen das Feld noch nicht; dort galt der Modus des Laufs.)
    resume = (mode == "full" and "cursor" in state
              and state.get("cursor_mode", state.get("mode")) == "full")
    pass_no = int(state.get("pass", "0")) + (0 if resume or mode == "update" else 1)
    started = float(state["started_at"]) if resume else clock()
    if resume:
        cursor: int | None = int(state["cursor"])
    elif mode == "update":
        cursor = int((float(state["finished_at"]) - MARGIN) * 1000)
    else:
        cursor = None
    await store(lambda: snapshot.set_state(
        status="running", mode=mode, started_at=started, error=None,
        **({"pass": pass_no} if mode == "full" else {}),
        **({} if resume else {"done": 0, "total": None, "cursor": None, "cursor_mode": None,
                              "skipped": 0})))
    done = int(float(state.get("done", "0"))) if resume else 0
    skipped = int(float(state.get("skipped", "0"))) if resume else 0
    expected = int(float(state["total"])) if resume and "total" in state else None
    skip = 0
    snapshot.busy = True
    log.info("Abzug %s beginnt (%s%s)", snapshot.path.stem, mode,
             ", Fortsetzung" if resume else "")
    try:
        while True:
            criteria = [Criterion("ngsearchword", ("*",))]
            if cursor is not None:
                criteria.append(Criterion(since, (str(cursor),)))
            total, nodes = await _page(client, sleep, criteria=criteria, sort=sort,
                                       max_items=page, skip=skip, properties=PROPERTIES)
            items = []
            for node in nodes:
                try:
                    item = features(node, secret)
                except Exception:  # ein kaputter Knoten hält den Abzug nicht auf
                    item = None
                if item is None:
                    skipped += 1
                else:
                    items.append(item)
            # der Durchlauf der Knoten bleibt beim Nachziehen der des letzten ganzen Abzugs
            await store(snapshot.put, items, pass_no)
            del items
            last = next((t for n in reversed(nodes)
                         if (t := millis(n, node_key, sort)) is not None), None)
            count = len(nodes)
            del nodes
            if expected is None:
                expected = total
                await store(_set, snapshot, {"total": expected})
            done += count
            if skip + count >= total:
                break  # das Repositorium hat zu dieser Anfrage nichts mehr
            if count < page:
                # weniger als verlangt, obwohl es mehr gibt: nicht als Ende deuten, sonst
                # verschwände am Schluss alles, was noch nicht gelesen ist
                raise SyncError("Das Repositorium hat eine unvollständige Seite geliefert. "
                                "Der Abzug lässt sich fortsetzen.")
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
            await store(_set, snapshot, {"cursor": cursor, "cursor_mode": mode,
                                         "done": done, "skipped": skipped})
            if pause:
                await sleep(pause)
        gone = 0
        if mode == "full":
            seen = await store(snapshot.seen, pass_no)
            # gegen den Bestand von jetzt prüfen, nicht gegen den vom Beginn des Laufs
            current, _ = await _page(client, sleep, criteria=[Criterion("ngsearchword", ("*",))],
                                     sort=sort, max_items=1, skip=0, properties=PROPERTIES[:1])
            if seen + skipped < current * COMPLETE:
                # Zeiger verwerfen: Der nächste Start beginnt neu, statt hier festzuhängen
                await store(_set, snapshot, {"cursor": None, "cursor_mode": None,
                                             "total": None})
                raise SyncError(
                    f"Der Durchgang hat nur {seen} von {current} Materialien gesehen. Der "
                    "alte Bestand bleibt; ein neuer Start liest von vorn.")
            gone = await store(snapshot.finish_pass, pass_no)
        await store(lambda: snapshot.set_state(
            status="ok", finished_at=started, cursor=None, cursor_mode=None, done=done,
            skipped=skipped))
        log.info("Abzug %s fertig: %d gelesen, %d entfernt, %d übergangen",
                 snapshot.path.stem, done, gone, skipped)
        return {"count": await store(snapshot.count), "removed": gone, "fetched": done,
                "skipped": skipped}
    except asyncio.CancelledError:
        snapshot.set_state(status="stopped")
        log.info("Abzug %s angehalten bei %d", snapshot.path.stem, done)
        raise
    except (EduSharingError, SyncError) as exc:
        message = exc.message if isinstance(exc, EduSharingError) else str(exc)
        log.warning("Abzug %s abgebrochen: %s", snapshot.path.stem, message[:300])
        await store(lambda: snapshot.set_state(status="failed", error=message[:300]))
        raise
    except Exception as exc:
        # nur die Art des Fehlers: Die Meldung könnte Inhalte eines Knotens tragen
        log.error("Abzug %s gescheitert (%s)", snapshot.path.stem, type(exc).__name__)
        await store(lambda: snapshot.set_state(
            status="failed", error="Unerwarteter Fehler beim Abzug."))
        raise
    finally:
        snapshot.busy = False
