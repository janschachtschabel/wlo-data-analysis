"""Läufe: alle Abfragen einer Definition ausführen und die Tabellen mit Zeitstempel ablegen.

Ein Lauf enthält nur Ergebnisse der Abfrage-Engine, also Zählungen, nie Datensätze.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from pydantic import ValidationError

from ..edusharing.client import EduSharingClient
from ..edusharing.errors import EduSharingError
from ..edusharing.vocab import UnresolvedValue, VocabStore
from ..engine.engine import run_query
from ..engine.spec import Group, QuerySpec, Rows
from ..errors import QueryError, UnknownInstance, describe_validation
from ..external.oersi import OersiClient, notation
from ..filters import FilterIn
from . import points as points_module
from .db import Database
from .definitions import Definition, NotFound, fetch, new_id, now_iso
from .points import Point, points_of, write_points
from .specs import (
    DashboardSpec,
    DataSection,
    FilterSpec,
    InvalidDefinition,
    MatrixSection,
    MetricSpec,
    OersiBlock,
    QueryBlock,
    TemplateSpec,
    View,
    dump_spec,
    parse_spec,
)

log = logging.getLogger(__name__)


class ServicesLike(Protocol):
    def client(self, instance_id: str) -> EduSharingClient: ...
    def vocab(self, instance_id: str) -> VocabStore: ...
    @property
    def oersi(self) -> OersiClient: ...


@dataclass
class Planned:
    key: str
    title: str
    view: dict[str, Any]
    query: QuerySpec | None = None
    error: dict[str, Any] | None = None
    external: dict[str, Any] | None = None  # Abruf außerhalb des Repositoriums


def _error(kind: str, detail: str, **extra: Any) -> dict[str, Any]:
    return {"detail": detail, "kind": kind, **extra}


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _slim(result: dict[str, Any], query: Any) -> dict[str, Any]:
    """Die Abfrage steht schon am Ergebnis-Eintrag; in ``provenance`` wird sie nicht noch
    einmal gespeichert."""
    provenance = result.get("provenance")
    if isinstance(provenance, dict) and "spec" in provenance and provenance["spec"] == query:
        return {**result, "provenance": {k: v for k, v in provenance.items() if k != "spec"}}
    return result


def _restore(result: dict[str, Any] | None, query: Any) -> dict[str, Any] | None:
    provenance = result.get("provenance") if isinstance(result, dict) else None
    if isinstance(provenance, dict) and "spec" not in provenance and "fetched_at" in provenance:
        provenance["spec"] = query
    return result


def fingerprint(results: list[dict[str, Any]]) -> str:
    """Kurzform dessen, was ein Lauf tatsächlich gefragt hat: die Abfragen samt der Filter,
    die aus gespeicherten Filtern und Kennzahlen hineingeflossen sind. Ändert sich eine davon,
    ändert sich die Kurzform, auch wenn die Definition selbst gleich blieb."""
    asked = [[r.get("key"), r.get("query")] for r in results]
    return hashlib.sha256(
        json.dumps(asked, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:16]


def load_run(con: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
    """Ein gespeicherter Lauf mit seinen Ergebnissen."""
    run = Runner._head(row)
    del run["seq"]
    run.update(spec=json.loads(row["spec"]), saved=True, results=[])
    for r in con.execute(
            "SELECT * FROM run_results WHERE run_id=? ORDER BY position", (row["id"],)):
        item = json.loads(r["query"])
        run["results"].append({
            "key": r["key"], "title": r["title"], "view": item["view"],
            "query": item["query"],
            "result": _restore(json.loads(r["result"]), item["query"])
            if r["result"] is not None else None,
            "error": json.loads(r["error"]) if r["error"] is not None else None})
    return run


def _points(run: dict[str, Any]) -> list[Point] | None:
    """Punkte eines Laufs; ein Fehler dabei kostet nur die Historie dieses Laufs."""
    try:
        points = points_of(run)
    except Exception:
        log.exception("Punkte des Laufs %s ließen sich nicht bilden", run.get("id"))
        return None
    if points is None:
        log.warning("Lauf %s hat mehr als %s Zahlen und bleibt ohne Punkte für die Historie",
                    run.get("id"), points_module.MAX_POINTS)
    return points


def backfill_points(db: Database) -> int:
    """Trägt für ältere Läufe die Punkte nach (einmal je Datenbank). Liefert die Zahl der
    nachgetragenen Läufe; unlesbare Läufe werden übergangen."""
    if db.read_now(lambda con: con.execute(
            "SELECT 1 FROM meta WHERE key='points_backfilled'").fetchone()):
        return 0
    ids = db.read_now(lambda con: [r["id"] for r in con.execute(
        "SELECT id FROM runs WHERE id NOT IN (SELECT DISTINCT run_id FROM points)"
        " ORDER BY seq")])
    done = 0
    if len(ids) > 50:
        log.info("Trage die Zahlen von %s älteren Läufen für die Historie nach …", len(ids))
    for run_id in ids:
        def one(con: sqlite3.Connection, run_id: str = run_id) -> bool:
            row = con.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
            if row is None:
                return False
            try:
                run = load_run(con, row)
            except (ValueError, KeyError, TypeError):
                log.warning("Lauf %s ist unlesbar und bleibt ohne Punkte", run_id)
                return False
            if row["fingerprint"] is None:
                con.execute("UPDATE runs SET fingerprint=? WHERE id=?",
                            (fingerprint(run["results"]), run_id))
            points = _points(run)
            if not points:
                return False
            write_points(con, run_id, points)
            return True

        done += bool(db.write_now(one))
    db.write_now(lambda con: con.execute(
        "INSERT OR REPLACE INTO meta (key, value) VALUES ('points_backfilled', '1')"))
    if done:
        log.info("Zahlen von %s älteren Läufen für die Historie nachgetragen", done)
    return done


def backfill_instances(db: Database) -> int:
    """Ältere Läufe wissen nicht, ob sie gegen eine andere Instanz liefen als ihre Definition.
    Das steht aber in ihren Abfragen: Weicht deren Instanz von der der Definition ab, wird
    sie am Lauf vermerkt, damit er nicht in der Historie der üblichen Instanz steht (einmal
    je Datenbank). Liefert die Zahl der geänderten Läufe."""
    if db.read_now(lambda con: con.execute(
            "SELECT 1 FROM meta WHERE key='instances_backfilled'").fetchone()):
        return 0

    def fn(con: sqlite3.Connection) -> int:
        usual: dict[str, set[str] | None] = {}
        seen_own: set[str] = set()  # Definitionen, die schon gegen ihre heutige Instanz liefen
        changed = 0
        for row in con.execute("SELECT id, definition_id FROM runs WHERE instance IS NULL"
                               " ORDER BY seq").fetchall():
            def_id = row["definition_id"]
            if def_id not in usual:
                try:
                    usual[def_id] = {p.query.instance for p in Runner._plan(
                        con, fetch(con, def_id), None) if p.query is not None}
                except Exception:  # gelöscht oder nicht mehr gültig: nichts zu vergleichen
                    usual[def_id] = None
            ran: set[str] = set()
            for r in con.execute("SELECT query FROM run_results WHERE run_id=?", (row["id"],)):
                try:
                    query = (json.loads(r["query"]) or {}).get("query") or {}
                except (ValueError, TypeError, AttributeError):
                    continue
                if isinstance(query, dict) and isinstance(query.get("instance"), str):
                    ran.add(query["instance"])
            own = usual[def_id]
            if own and ran == own:
                seen_own.add(def_id)
            # Abweichend ist ein Lauf nur, wenn die Definition vorher schon gegen ihre
            # heutige Instanz lief. Sonst wurde sie später umgestellt, und die älteren Läufe
            # waren zu ihrer Zeit die üblichen.
            elif (own and len(ran) == 1 and len(own) == 1 and def_id in seen_own):
                con.execute("UPDATE runs SET instance=? WHERE id=?", (next(iter(ran)), row["id"]))
                changed += 1
        con.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES ('instances_backfilled', '1')")
        return changed

    changed = db.write_now(fn)
    if changed:
        log.info("%s ältere Läufe ihrer abweichenden Instanz zugeordnet", changed)
    return changed


class DamagedRun(Exception):
    """Ein gespeicherter Lauf lässt sich nicht lesen oder darstellen."""


class Runner:
    def __init__(self, db: Database, services: ServicesLike, keep: int = 500,
                 clock: Callable[[], str] = now_iso) -> None:
        self._db = db
        self._services = services
        self._keep = keep
        self._clock = clock

    # ---- Planung ---------------------------------------------------------------

    @staticmethod
    def _plan(con: sqlite3.Connection, definition: Definition, instance: str | None
              ) -> list[Planned]:
        spec = parse_spec(definition.kind, definition.spec)
        if isinstance(spec, FilterSpec):
            raise InvalidDefinition(
                "Ein Filter lässt sich nicht ausführen; er gehört in eine Kennzahl, ein "
                "Dashboard oder ein Template.")
        filters: dict[str, tuple[str, FilterSpec]] = {}

        def stored_filter(fid: str) -> tuple[str, FilterSpec]:
            if fid not in filters:
                found = fetch(con, fid)
                filters[fid] = (found.name, parse_spec("filter", found.spec))  # type: ignore[assignment]
            return filters[fid]

        def build(key: str, title: str, query: QuerySpec, filter_ids: list[str],
                  view: View) -> Planned:
            planned = Planned(key, title, view.model_dump(mode="json"))
            target = instance or getattr(spec, "instance", None) or query.instance
            base: list[FilterIn] = []
            used: dict[str, str] = {f.property: "die Abfrage selbst" for f in query.filters}
            try:
                for fid in dict.fromkeys(filter_ids):
                    name, flt = stored_filter(fid)
                    if flt.instance and flt.instance != target:
                        raise QueryError(
                            f"Der Filter '{name}' gilt nur für die Instanz '{flt.instance}', "
                            f"die Abfrage läuft gegen '{target}'.")
                    for item in flt.filters:
                        if item.property in used:
                            raise QueryError(
                                f"Der Filter '{name}' und {used[item.property]} filtern beide "
                                f"das Feld '{item.property}'. Das Repositorium würde nur eines "
                                "davon auswerten.")
                        used[item.property] = f"der Filter '{name}'"
                        base.append(item)
                planned.query = query.model_copy(update={
                    "instance": target, "filters": base + list(query.filters)})
            except (QueryError, NotFound) as exc:
                planned.error = _error("invalid_query", str(exc))
            return planned

        if isinstance(spec, MetricSpec):
            return [build("main", definition.name, spec.query, spec.filter_ids, spec.view)]
        items: list[Any]
        if isinstance(spec, DashboardSpec):
            items = list(spec.tiles)
        else:
            assert isinstance(spec, TemplateSpec)
            items = [s for s in spec.sections if isinstance(s, DataSection | MatrixSection)]
        plan = []
        for item in items:
            if isinstance(item, MatrixSection):
                plan += Runner._plan_matrix(item, instance or spec.instance or item.instance,
                                            list(spec.filter_ids), build)
                continue
            query, filter_ids, view = item.query, list(spec.filter_ids), item.view
            if item.metric_id:
                try:
                    metric = parse_spec("metric", fetch(con, item.metric_id).spec)
                except NotFound as exc:
                    plan.append(Planned(item.id, item.title, View().model_dump(mode="json"),
                                        error=_error("invalid_query", str(exc))))
                    continue
                assert isinstance(metric, MetricSpec)
                query, view = metric.query, view or metric.view
                filter_ids = metric.filter_ids + filter_ids
            assert query is not None
            plan.append(build(item.id, item.title, query, filter_ids, view or View()))
        return plan

    @staticmethod
    def _plan_matrix(section: MatrixSection, instance: str | None, filter_ids: list[str],
                     build: Callable[..., Planned]) -> list[Planned]:
        """Je Abfrage-Block zwei Abfragen: die Einzelzeilen als Facette und die
        Zwischensummen als Gruppen (jedes Material einmal je Gruppe)."""
        plan = []
        rows = section.rows
        listed = [v for s in rows.sections for v in s.values]
        for block in section.blocks:
            prefix = f"{section.id}/{block.id}"
            title = f"{section.title} – {block.label}"
            if isinstance(block, OersiBlock):
                wanted = {
                    s.id: [n for v in s.values + s.extra
                           if (n := notation(v if isinstance(v, str) else v.value))]
                    for s in rows.sections}
                plan.append(Planned(prefix + "/oersi", title, View().model_dump(mode="json"),
                                    external={"kind": "oersi", "sections": wanted}))
                continue
            if not isinstance(block, QueryBlock):
                continue  # Handeinträge stehen in der Definition, es gibt nichts abzurufen
            if instance is None:
                plan.append(Planned(prefix + "/rows", title, View().model_dump(mode="json"),
                                    error=_error("invalid_query",
                                                 "Für diese Tabelle ist keine Instanz "
                                                 "angegeben (am Template oder beim Lauf).")))
                continue
            try:
                detail = QuerySpec(
                    instance=instance, filters=block.filters, series=section.series,
                    rows=Rows(property=rows.property, include=listed or None, sort="given",
                              missing=rows.missing) if listed or rows.missing else None)
                groups = QuerySpec(
                    instance=instance, filters=block.filters, series=section.series,
                    rows=Rows(property=rows.property, sort="given", groups=[
                        Group(id=s.id, label=s.label, values=s.values + s.extra)
                        for s in rows.sections])) if rows.subtotal == "distinct" else None
            except ValidationError as exc:  # z. B. mehr Werte, als eine Abfrage nimmt
                plan.append(Planned(prefix + "/rows", title, View().model_dump(mode="json"),
                                    error=_error("invalid_query",
                                                 describe_validation(exc.errors()))))
                continue
            plan.append(build(prefix + "/rows", title, detail, filter_ids, View()))
            if groups is not None:
                plan.append(build(prefix + "/groups", title, groups, filter_ids, View()))
        return plan

    # ---- Ausführung ------------------------------------------------------------

    async def _execute(self, planned: Planned, refresh: bool) -> dict[str, Any]:
        out: dict[str, Any] = {
            "key": planned.key, "title": planned.title, "view": planned.view,
            "query": (planned.query.model_dump(mode="json", exclude={"refresh"})
                      if planned.query else None),
            "result": None, "error": planned.error}
        if planned.external is not None and planned.error is None:
            try:
                out["result"] = await self._services.oersi.counts(
                    planned.external["sections"], refresh=refresh)
            except EduSharingError as exc:
                out["error"] = _error(exc.kind, exc.message, upstream_status=exc.status)
            except Exception:
                log.exception("Unerwarteter Fehler beim Abruf von OERSI (%s)", planned.key)
                out["error"] = _error("internal", "Unerwarteter Fehler bei dieser Abfrage.")
            return out
        if planned.query is None:
            return out
        query = planned.query.model_copy(update={"refresh": refresh})
        try:
            client = self._services.client(query.instance)
            vocab = self._services.vocab(query.instance)
        except KeyError:
            out["error"] = _error("unknown_instance",
                                  f"Die Instanz '{query.instance}' gibt es nicht.")
            return out
        try:
            out["result"] = await run_query(
                query, client, vocab,
                self._services.snapshot(query.instance) if query.snapshot else None)
        except QueryError as exc:
            out["error"] = _error("invalid_query", str(exc))
        except UnresolvedValue as exc:
            out["error"] = _error("unresolved_value", str(exc), property=exc.property,
                                  text=exc.text, ambiguous=exc.ambiguous,
                                  suggestions=exc.suggestions)
        except EduSharingError as exc:
            out["error"] = _error(exc.kind, exc.message, upstream_status=exc.status)
        except Exception:
            # Der Lauf wird in jedem Fall gespeichert; eine Kachel reißt die anderen nicht mit.
            log.exception("Unerwarteter Fehler in Kachel %s", planned.key)
            out["error"] = _error("internal", "Unerwarteter Fehler bei dieser Abfrage.")
        return out

    async def preview(self, kind: str, name: str, spec: Any, *, refresh: bool = False,
                      instance: str | None = None) -> dict[str, Any]:
        """Führt eine ungespeicherte Definition aus; nichts wird abgelegt."""
        now = now_iso()
        draft = Definition("", kind, name, "", None,
                           json.loads(dump_spec(parse_spec(kind, spec))), 0, now, now)
        return await self.run("", refresh=refresh, instance=instance, save=False, draft=draft)

    async def run(self, def_id: str, *, refresh: bool = False, instance: str | None = None,
                  save: bool = True, draft: Definition | None = None,
                  origin: str = "manual", run_id: str | None = None,
                  progress: Callable[[int, int], None] | None = None) -> dict[str, Any]:
        """``progress(erledigt, gesamt)`` wird nach jeder Abfrage gerufen; ``run_id`` legt die
        ID des gespeicherten Laufs vorab fest (für Aufträge)."""
        if instance is not None:
            try:
                self._services.client(instance)
            except KeyError:
                raise UnknownInstance(instance) from None

        def prepare(con: sqlite3.Connection) -> tuple[Definition, list[Planned], bool]:
            definition = draft or fetch(con, def_id)
            plan = self._plan(con, definition, instance)
            own = False
            if instance is not None:
                # Nennt jemand ausdrücklich die Instanz, gegen die die Definition ohnehin
                # läuft, ist das kein abweichender Lauf.
                usual = [p.query.instance for p in self._plan(con, definition, None)
                         if p.query is not None]
                own = bool(usual) and all(name == instance for name in usual)
            return definition, plan, own

        definition, plan, own = await self._db.read(prepare)
        stored_instance = None if own else instance
        save = save and draft is None
        started = self._clock()
        # Alle Kacheln nebeneinander; der Client begrenzt die gleichzeitigen Abfragen.
        done = 0

        async def one(planned: Planned) -> dict[str, Any]:
            nonlocal done
            out = await self._execute(planned, refresh)
            done += 1
            if progress is not None:
                progress(done, len(plan))
            return out

        if progress is not None:
            progress(0, len(plan))
        results = list(await asyncio.gather(*[one(p) for p in plan]))
        failed = sum(1 for r in results if r["error"] is not None)
        status = "ok" if not failed else "failed" if failed == len(results) else "partial"
        run = {
            # ein ungespeicherter Lauf ist nirgends abrufbar und hat deshalb keine ID
            "id": (run_id or new_id()) if save else None,
            "definition_id": definition.id or None, "kind": definition.kind,
            "name": definition.name, "definition_version": definition.version,
            "spec": definition.spec, "status": status, "started_at": started,
            "finished_at": self._clock(), "origin": origin, "instance": stored_instance,
            "fingerprint": fingerprint(results), "saved": save, "results": results}
        if save:
            points = await asyncio.to_thread(_points, run)
            await self._db.write(lambda con: self._store(con, run, points))
        return run

    def _store(self, con: sqlite3.Connection, run: dict[str, Any],
               points: list[Point] | None = None) -> None:
        # Die Definition kann inzwischen gelöscht sein; der Lauf bleibt dann ohne Bezug.
        exists = con.execute("SELECT 1 FROM definitions WHERE id=?",
                             (run["definition_id"],)).fetchone()
        if not exists:
            run["definition_id"] = None
        con.execute(
            "INSERT INTO runs (id, definition_id, kind, name, definition_version, spec, status,"
            " started_at, finished_at, origin, instance, fingerprint)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (run["id"], run["definition_id"], run["kind"], run["name"],
             run["definition_version"], _dumps(run["spec"]), run["status"], run["started_at"],
             run["finished_at"], run["origin"], run["instance"], run["fingerprint"]))
        if points:
            write_points(con, run["id"], points)
        con.executemany(
            "INSERT INTO run_results (run_id, key, position, title, query, result, error)"
            " VALUES (?,?,?,?,?,?,?)",
            [(run["id"], r["key"], pos, r["title"],
              _dumps({"query": r["query"], "view": r["view"]}),
              _dumps(_slim(r["result"], r["query"])) if r["result"] is not None else None,
              _dumps(r["error"]) if r["error"] is not None else None)
             for pos, r in enumerate(run["results"])])
        if run["definition_id"] is not None:
            con.execute(
                "DELETE FROM runs WHERE definition_id=? AND seq NOT IN ("
                " SELECT seq FROM runs WHERE definition_id=? ORDER BY seq DESC LIMIT ?)",
                (run["definition_id"], run["definition_id"], self._keep))

    # ---- Lesen -----------------------------------------------------------------

    @staticmethod
    def _head(row: sqlite3.Row) -> dict[str, Any]:
        return {"id": row["id"], "seq": row["seq"], "definition_id": row["definition_id"],
                "kind": row["kind"], "name": row["name"],
                "definition_version": row["definition_version"], "status": row["status"],
                "started_at": row["started_at"], "finished_at": row["finished_at"],
                "origin": row["origin"], "instance": row["instance"],
                "fingerprint": row["fingerprint"]}

    async def runs(self, definition_id: str | None = None, limit: int = 50,
                   before: int | None = None, status: str | None = None,
                   kind: str | None = None) -> list[dict[str, Any]]:
        """Neueste zuerst, ohne Ergebnisse. ``before`` ist das ``seq`` des letzten Eintrags
        der vorigen Seite."""
        where, args = [], []
        if definition_id == "":
            where.append("definition_id IS NULL")  # Läufe, deren Definition gelöscht ist
        elif definition_id is not None:
            where.append("definition_id=?")
            args.append(definition_id)
        if before is not None:
            where.append("seq < ?")
            args.append(before)
        for column, wanted in (("status", status), ("kind", kind)):
            if wanted is not None:
                where.append(f"{column}=?")
                args.append(wanted)
        sql = ("SELECT * FROM runs"  # noqa: S608
               + (" WHERE " + " AND ".join(where) if where else "")
               + " ORDER BY seq DESC LIMIT ?")
        args.append(max(1, min(limit, 500)))
        return await self._db.read(
            lambda con: [self._head(r) for r in con.execute(sql, args)])

    async def get_run(self, run_id: str) -> dict[str, Any]:
        def fn(con: sqlite3.Connection) -> dict[str, Any]:
            row = con.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
            if row is None:
                raise NotFound("Den Lauf gibt es nicht.")
            try:
                return load(con, row)
            except (ValueError, KeyError, TypeError):
                log.exception("Lauf %s lässt sich nicht lesen", run_id)
                raise DamagedRun(
                    "Der gespeicherte Lauf ist beschädigt und lässt sich nicht lesen. "
                    "Bitte die Definition neu ausführen.") from None

        load = load_run

        return await self._db.read(fn)

    async def delete_runs(self, definition_id: str) -> int:
        """Alle Läufe einer Definition; ``""`` meint die Läufe gelöschter Definitionen."""
        def fn(con: sqlite3.Connection) -> int:
            if definition_id == "":
                return con.execute("DELETE FROM runs WHERE definition_id IS NULL").rowcount
            return con.execute(
                "DELETE FROM runs WHERE definition_id=?", (definition_id,)).rowcount

        return await self._db.write(fn)

    async def delete_run(self, run_id: str) -> None:
        def fn(con: sqlite3.Connection) -> None:
            if con.execute("DELETE FROM runs WHERE id=?", (run_id,)).rowcount == 0:
                raise NotFound("Den Lauf gibt es nicht.")

        await self._db.write(fn)
