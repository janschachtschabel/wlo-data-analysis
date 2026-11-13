"""Läufe: alle Abfragen einer Definition ausführen und die Tabellen mit Zeitstempel ablegen.

Ein Lauf enthält nur Ergebnisse der Abfrage-Engine, also Zählungen, nie Datensätze.
"""

from __future__ import annotations

import asyncio
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
from ..errors import QueryError, describe_validation
from ..external.oersi import OersiClient, notation
from ..filters import FilterIn
from .db import Database
from .definitions import Definition, NotFound, fetch, new_id, now_iso
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
            out["result"] = await run_query(query, client, vocab)
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

    async def run(self, def_id: str, *, refresh: bool = False, instance: str | None = None,
                  save: bool = True) -> dict[str, Any]:
        if instance is not None:
            try:
                self._services.client(instance)
            except KeyError:
                raise InvalidDefinition(
                    f"Die Instanz '{instance[:64]}' gibt es nicht.") from None

        def prepare(con: sqlite3.Connection) -> tuple[Definition, list[Planned]]:
            definition = fetch(con, def_id)
            return definition, self._plan(con, definition, instance)

        definition, plan = await self._db.read(prepare)
        started = self._clock()
        # Alle Kacheln nebeneinander; der Client begrenzt die gleichzeitigen Abfragen.
        results = list(await asyncio.gather(*[self._execute(p, refresh) for p in plan]))
        failed = sum(1 for r in results if r["error"] is not None)
        status = "ok" if not failed else "failed" if failed == len(results) else "partial"
        run = {
            "id": new_id(), "definition_id": definition.id, "kind": definition.kind,
            "name": definition.name, "definition_version": definition.version,
            "spec": definition.spec, "status": status, "started_at": started,
            "finished_at": self._clock(), "saved": save, "results": results}
        if save:
            await self._db.write(lambda con: self._store(con, run))
        return run

    def _store(self, con: sqlite3.Connection, run: dict[str, Any]) -> None:
        # Die Definition kann inzwischen gelöscht sein; der Lauf bleibt dann ohne Bezug.
        exists = con.execute("SELECT 1 FROM definitions WHERE id=?",
                             (run["definition_id"],)).fetchone()
        if not exists:
            run["definition_id"] = None
        con.execute(
            "INSERT INTO runs (id, definition_id, kind, name, definition_version, spec, status,"
            " started_at, finished_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (run["id"], run["definition_id"], run["kind"], run["name"],
             run["definition_version"], _dumps(run["spec"]), run["status"], run["started_at"],
             run["finished_at"]))
        con.executemany(
            "INSERT INTO run_results (run_id, key, position, title, query, result, error)"
            " VALUES (?,?,?,?,?,?,?)",
            [(run["id"], r["key"], pos, r["title"],
              _dumps({"query": r["query"], "view": r["view"]}),
              _dumps(r["result"]) if r["result"] is not None else None,
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
                "started_at": row["started_at"], "finished_at": row["finished_at"]}

    async def runs(self, definition_id: str | None = None, limit: int = 50,
                   before: int | None = None) -> list[dict[str, Any]]:
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

        def load(con: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
            run = self._head(row)
            del run["seq"]
            run.update(spec=json.loads(row["spec"]), saved=True, results=[])
            for r in con.execute(
                    "SELECT * FROM run_results WHERE run_id=? ORDER BY position", (run_id,)):
                item = json.loads(r["query"])
                run["results"].append({
                    "key": r["key"], "title": r["title"], "view": item["view"],
                    "query": item["query"],
                    "result": json.loads(r["result"]) if r["result"] is not None else None,
                    "error": json.loads(r["error"]) if r["error"] is not None else None})
            return run

        return await self._db.read(fn)

    async def delete_run(self, run_id: str) -> None:
        def fn(con: sqlite3.Connection) -> None:
            if con.execute("DELETE FROM runs WHERE id=?", (run_id,)).rowcount == 0:
                raise NotFound("Den Lauf gibt es nicht.")

        await self._db.write(fn)
