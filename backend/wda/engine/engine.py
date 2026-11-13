"""Führt eine QuerySpec aus.

Zerlegung (Zähl-Semantik siehe docs/edu-sharing-api-erkenntnisse.md):

* Je Spalte mit Filtern eine Aggregat-Abfrage: Grundfilter UND Spaltenfilter, Facette über
  das Zeilenfeld. ``pagination.total`` ist die Gesamtzahl der Spalte (jedes Dokument einmal).
* Gruppen im Modus ``sum`` addieren Facettenwerte (exakt nur bei einwertigen Feldern),
  Gruppen im Modus ``distinct`` kosten je Gruppe und Spalte eine Zählabfrage.
* „Ohne Wert" = Gesamtzahl minus Zählung mit allen vorkommenden Werten als ein Kriterium.
* Spalten mit Formel werden je Zeile aus den anderen Spalten berechnet.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from typing import Any

from ..edusharing.client import MAX_VALUES, Aggregate, Criterion, EduSharingClient
from ..edusharing.vocab import VocabStore
from ..errors import Notes, QueryError
from ..filters import (
    FilterIn,
    check_prefix_shape,
    check_prefix_values,
    resolve_filters,
    resolve_values,
)
from .formula import Formula, compile_formula
from .spec import MAX_CALLS, MAX_SERIES, QuerySpec, Series

Number = int | float
SCHEMA_VERSION = 1  # Form des Ergebnisses; gespeicherte Läufe tragen sie mit


@dataclass
class _Column:
    id: str
    label: str
    criteria: tuple[Criterion, ...] = ()
    formula: Formula | None = None
    filters: list[dict[str, Any]] = field(default_factory=list)
    total: Number | None = None
    cells: dict[str, Number | None] = field(default_factory=dict)

    @property
    def derived(self) -> bool:
        return self.formula is not None


@dataclass
class _Row:
    key: str
    label: str
    kind: str  # value | group | other | missing
    members: tuple[str, ...] = ()


class _Run:
    """Zählt und bündelt die Aggregat-Aufrufe einer Abfrage."""

    def __init__(self, client: EduSharingClient, content_type: str, refresh: bool) -> None:
        self._client = client
        self._content_type = content_type
        self._refresh = refresh
        self._tasks: dict[tuple, asyncio.Task[Aggregate]] = {}
        self.results: list[Aggregate] = []

    def call(self, criteria: tuple[Criterion, ...], facet: str | None = None
             ) -> asyncio.Task[Aggregate]:
        key = (tuple(sorted((c.property, tuple(sorted(c.values))) for c in criteria)), facet)
        if key not in self._tasks:
            self._tasks[key] = asyncio.ensure_future(self._do(criteria, facet))
        return self._tasks[key]

    async def _do(self, criteria: tuple[Criterion, ...], facet: str | None) -> Aggregate:
        result = await self._client.aggregate(
            criteria, [facet] if facet else [], content_type=self._content_type,
            refresh=self._refresh)
        self.results.append(result)
        return result

    async def cancel(self) -> None:
        for task in self._tasks.values():
            task.cancel()
        await asyncio.gather(*self._tasks.values(), return_exceptions=True)


async def run_query(spec: QuerySpec, client: EduSharingClient, vocab: VocabStore
                    ) -> dict[str, Any]:
    run = _Run(client, spec.content_type, spec.refresh)
    try:
        return await _execute(spec, client, vocab, run)
    except BaseException:
        await run.cancel()
        raise


async def _execute(spec: QuerySpec, client: EduSharingClient, vocab: VocabStore, run: _Run
                   ) -> dict[str, Any]:
    notes = Notes()
    prefix_fields = set(client.instance.prefix_fields)
    base_list, base_described = await resolve_filters(vocab, spec.filters)
    base = tuple(base_list)
    base_props = {c.property for c in base}
    rows_spec = spec.rows
    row_prop = rows_spec.property if rows_spec else None
    # Einwertiges Feld: Facettenwerte summieren sich exakt; ein Kriterium nimmt nur einen Wert.
    single = row_prop in prefix_fields

    # ---- Zeilenplanung (ohne Aufrufe) ----------------------------------------
    mode = "facet"
    groups: list[_Row] = []
    include: list[dict[str, str]] | None = None
    include_map: list[str] | None = None
    if rows_spec:
        await vocab.check_facet(rows_spec.property)
        if rows_spec.groups:
            mode = rows_spec.group_mode
            if mode == "auto":
                mode = "sum" if single else "distinct"
            if mode == "distinct" and single:
                raise QueryError(
                    f"'{row_prop}' ist einwertig: Gruppen gehen hier nur als Summe "
                    "(group_mode sum oder auto).")
            for group in rows_spec.groups:
                members = await resolve_values(vocab, rows_spec.property, group.values)
                groups.append(_Row(group.id or _slug(group.label), group.label, "group",
                                   tuple(m["value"] for m in members)))
            if len({g.key for g in groups}) != len(groups):
                raise QueryError(
                    "Die IDs der Gruppen müssen eindeutig sein (ohne ID entsteht sie aus dem "
                    "Namen der Gruppe).")
        if rows_spec.include:
            include = await resolve_values(vocab, rows_spec.property, rows_spec.include)
            # je Eingabe der aufgelöste Wert, damit Aufrufer ihre Liste zuordnen können
            include_map = [(await resolve_values(vocab, rows_spec.property, [item]))[0]["value"]
                           for item in rows_spec.include]
        if row_prop in base_props:
            # Die Facette ignoriert den Filter auf dem eigenen Feld.
            if groups or rows_spec.missing or rows_spec.other:
                raise QueryError(
                    f"Das Zeilenfeld '{row_prop}' steht auch in den Filtern. Das geht nicht "
                    "zusammen mit Gruppen, „Übrige“ oder der Zeile „ohne Wert“.")
            notes.add("rows_filtered", 
                f"Das Zeilenfeld '{row_prop}' steht auch in den Filtern: Die Zeilen zeigen "
                "nur die gefilterten Werte.")

    # ---- Spaltenplanung und Grenzen (vor dem ersten Aufruf) -------------------
    n_planned = (min(spec.columns.top, MAX_SERIES) if spec.columns and not spec.columns.include
                 else len(spec.columns.include) if spec.columns
                 else sum(1 for s in spec.series if s.formula is None) or 1)
    per_column = 1
    if rows_spec:
        per_column += (1 if rows_spec.missing and not single else 0)
        per_column += (1 if rows_spec.other and not single else 0)
        per_column += (len(groups) if mode == "distinct" else 0)
    planned = n_planned * per_column + (1 if spec.columns else 0) + len(prefix_fields)
    if planned > MAX_CALLS:
        raise QueryError(
            f"Die Abfrage bräuchte bis zu {planned} Einzelabfragen, erlaubt sind {MAX_CALLS}. "
            "Weniger Spalten oder Gruppen wählen.")
    check_prefix_shape(base, prefix_fields)
    columns = await _plan_columns(spec, vocab, run, base, base_props, row_prop, notes)
    data_columns = [c for c in columns if not c.derived]
    for col in data_columns:
        check_prefix_shape(col.criteria, prefix_fields)
    async def occurring(prop: str):
        return (await run.call((), prop)).facets[prop]

    await check_prefix_values(
        occurring, [c for col in data_columns for c in col.criteria] + list(base),
        prefix_fields, notes, spec.columns.property if spec.columns else None)

    # ---- Ausführung -----------------------------------------------------------
    main = await asyncio.gather(*[
        run.call(base + col.criteria, row_prop if rows_spec else None) for col in data_columns])
    facet_counts: dict[str, dict[str, int]] = {}
    cut: set[str] = set()  # Spalten, deren Werteliste gekürzt ist
    for col, agg in zip(data_columns, main, strict=True):
        col.total = agg.total
        if rows_spec:
            facet = agg.facets[rows_spec.property]
            facet_counts[col.id] = {v.value: v.count for v in facet.values}
            if facet.truncated:
                cut.add(col.id)
    if cut:
        notes.add("facet_truncated", 
            f"Die Werteliste von '{row_prop}' wurde vom Repositorium gekürzt; "
            "seltene Werte fehlen.")

    def cell(col: _Column, value: str) -> int | None:
        fc = facet_counts[col.id]
        if value in fc:
            return fc[value]
        return None if col.id in cut else 0  # gekürzt: unbekannt statt 0

    rows: list[_Row] = []
    if rows_spec:
        if mode == "distinct":
            rows = groups
            counts = await asyncio.gather(*[
                run.call(base + col.criteria + (Criterion(rows_spec.property, g.members),))
                for col in data_columns for g in groups])
            it = iter(counts)
            for col in data_columns:
                for g in groups:
                    col.cells[g.key] = next(it).total
        elif mode == "sum":
            rows = groups
            for col in data_columns:
                for g in groups:
                    col.cells[g.key] = _sum(cell(col, m) for m in set(g.members))
            if not single:
                notes.add("groups_summed", 
                    "Gruppen sind Summen der Einzelwerte. Trägt ein Dokument mehrere Werte "
                    "einer Gruppe, zählt es mehrfach.")
        else:
            rows = await _value_rows(rows_spec, vocab, base, include, facet_counts, data_columns)
            for row in rows:
                row.members = (row.key,)
                for col in data_columns:
                    col.cells[row.key] = cell(col, row.key)
            if not single and len(rows) > 1:
                notes.add("rows_overlap", 
                    f"Ein Dokument kann mehrere Werte von '{row_prop}' tragen und steht dann "
                    "in mehreren Zeilen; die Zeilen summieren sich nicht zur Gesamtzahl.")

        if groups:
            grouped = {m for g in groups for m in g.members}
            loose = sorted({v for fc in facet_counts.values() for v, n in fc.items() if n}
                           - grouped)
            if loose:
                shown = ", ".join(f"'{v}'" if v else "(leer)" for v in loose[:5])
                notes.add("values_ungrouped", 
                    f"{len(loose)} vorkommende Werte von '{row_prop}' stehen in keiner Gruppe "
                    f"und sind nicht dargestellt ({shown}{' …' if len(loose) > 5 else ''}). "
                    "Die Zeilen summieren sich deshalb nicht zur Gesamtzahl.")

    # ---- Formeln, Sortierung, top/other, „ohne Wert" --------------------------
    _apply_formulas(columns, [r.key for r in rows])
    rows = _sort_rows(rows, columns, rows_spec, include, bool(groups))
    if rows_spec and rows_spec.top is not None and len(rows) > rows_spec.top:
        kept, rest = rows[: rows_spec.top], rows[rows_spec.top:]
        notes.add("rows_top",
                  f"Angezeigt werden die ersten {rows_spec.top} von {len(rows)} Zeilen.")
        if rows_spec.other:
            # Werte, die in keiner angezeigten Zeile stecken
            shown = {m for r in kept for m in r.members}
            rest_values = tuple(sorted({m for r in rest for m in r.members} - shown))
            other = _Row("__other__", "Übrige", "other")
            if not rest_values:
                for col in data_columns:
                    col.cells[other.key] = 0
            elif single:
                for col in data_columns:
                    col.cells[other.key] = _sum(cell(col, v) for v in rest_values)
            elif len(rest_values) > MAX_VALUES:
                other = None
                notes.add("other_dropped", "„Übrige“ entfällt: zu viele verschiedene Werte.")
            else:
                counted = await asyncio.gather(*[
                    run.call(base + col.criteria + (Criterion(rows_spec.property, rest_values),))
                    for col in data_columns])
                for col, agg in zip(data_columns, counted, strict=True):
                    col.cells[other.key] = agg.total
                notes.add("other_overlap", 
                    "„Übrige“ zählt die Dokumente mit mindestens einem der nicht gezeigten "
                    "Werte; ein Dokument kann zugleich in einer gezeigten Zeile stehen.")
            if other is not None:
                kept.append(other)
                _apply_formulas(columns, [other.key])
        rows = kept
    if rows_spec and rows_spec.missing:
        missing = _Row("__missing__", "ohne Wert", "missing")
        if single:
            # Einwertig: Die Facettenwerte summieren sich zur Zahl der Dokumente mit Wert.
            with_value: list[int | None] = [
                None if col.id in cut else sum(facet_counts[col.id].values())
                for col in data_columns]
        else:
            with_value = await asyncio.gather(*[
                _count_with_any_value(run, base + col.criteria, rows_spec.property,
                                      facet_counts[col.id], col.id in cut)
                for col in data_columns])
        for col, counted in zip(data_columns, with_value, strict=True):
            col.cells[missing.key] = (None if counted is None or col.total is None
                                      else max(col.total - counted, 0))
        if any(c is None for c in with_value):
            notes.add("missing_unknown", 
                "„Ohne Wert“ fehlt, wo die Werteliste gekürzt ist oder zu viele Werte hat.")
        rows.append(missing)
        _apply_formulas(columns, [missing.key])

    _apply_formula_totals(columns)
    if any(c.derived for c in columns):
        notes.add("derived_columns",
                  "Spalten mit Formel sind aus anderen Spalten berechnet, nicht gezählt.")
    if spec.percent == "row" and len(data_columns) > 1:
        notes.add("row_percent", 
            "Zeilenanteile beziehen sich auf die Summe der gezählten Spalten. Das ist nur "
            "sinnvoll, wenn sich die Spalten nicht überschneiden.")

    # ---- Ausgabe --------------------------------------------------------------
    out_rows = []
    for row in rows:
        values = [col.cells.get(row.key) for col in columns]
        entry: dict[str, Any] = {"key": row.key, "label": row.label, "kind": row.kind,
                                 "values": values}
        if spec.percent == "column":
            entry["percent"] = [None if col.derived else _share(v, col.total)
                                for v, col in zip(values, columns, strict=True)]
        elif spec.percent == "row":
            denominator = sum(v for v, col in zip(values, columns, strict=True)
                              if v is not None and not col.derived)
            entry["percent"] = [None if col.derived else _share(v, denominator)
                                for v, col in zip(values, columns, strict=True)]
        out_rows.append(entry)

    fetched = [r.fetched_at for r in run.results]
    inst = client.instance
    return {
        "schema_version": SCHEMA_VERSION,
        "instance": spec.instance,
        "columns": [{"id": c.id, "label": c.label, "derived": c.derived, "total": c.total}
                    for c in columns],
        "rows": out_rows,
        "provenance": {
            "fetched_at": max(fetched) if fetched else None,
            "oldest_fetched_at": min(fetched) if fetched else None,
            "cached": bool(run.results) and all(r.cached for r in run.results),
            "queries": len(run.results),
            "notes": list(notes),
            "instance": {"id": inst.id, "base_url": inst.base_url, "repository": inst.repository,
                         "metadataset": inst.metadataset, "query": inst.query},
            "spec": spec.model_dump(mode="json", exclude={"refresh"}),
            "rows": ({"property": row_prop, "mode": mode, "include": include_map,
                      "groups": [{"key": g.key, "label": g.label, "values": list(g.members)}
                                 for g in groups]} if rows_spec else None),
            "filters": base_described,
            "columns": [{"id": c.id, "filters": c.filters,
                         "formula": c.formula.text if c.formula else None} for c in columns],
        },
    }


def _slug(label: str) -> str:
    """Stabile ID aus dem Namen einer Gruppe, unabhängig von ihrer Position."""
    text = re.sub(r"[^a-z0-9]+", "_", label.casefold()).strip("_")
    return "g_" + (text or "x")[:36]


def _sum(values) -> int | None:
    total = 0
    for value in values:
        if value is None:
            return None
        total += value
    return total


async def _plan_columns(
    spec: QuerySpec, vocab: VocabStore, run: _Run, base: tuple[Criterion, ...] | list[Criterion],
    base_props: set[str], row_prop: str | None, notes: Notes,
) -> list[_Column]:
    base = tuple(base)
    if spec.columns:
        prop = spec.columns.property
        if prop == row_prop:
            raise QueryError("Zeilen und Spalten brauchen verschiedene Felder.")
        if prop in base_props:
            raise QueryError(
                f"Das Spaltenfeld '{prop}' steht auch in den Filtern. Die Werte dort "
                "stattdessen über include der Spalten auswählen.")
        await vocab.check_facet(prop)
        if spec.columns.include:
            chosen = await resolve_values(vocab, prop, spec.columns.include)
        else:
            facet = (await run.call(base, prop)).facets[prop]
            top = sorted(facet.values, key=lambda v: (-v.count, v.value))[: spec.columns.top]
            chosen = [{"value": v.value, "label": await vocab.label(prop, v.value)}
                      for v in top]
            if len(facet.values) > len(top) or facet.truncated:
                notes.add("columns_top", 
                    f"Spalten: die {len(top)} häufigsten Werte von '{prop}'; weitere "
                    "Werte sind nicht dargestellt.")
        # ID ist der Wert selbst: bleibt über Läufe gleich, auch wenn sich die Rangfolge ändert
        return [_Column(item["value"], item["label"],
                        (Criterion(prop, (item["value"],)),),
                        filters=[{"property": prop, "values": [item]}])
                for item in chosen]
    series = spec.series or [Series(id="anzahl", label="Anzahl")]
    columns: list[_Column] = []
    for item in series:
        if item.formula is not None:
            columns.append(_Column(item.id, item.label, formula=compile_formula(item.formula)))
            continue
        _check_series_filters(item.filters, base_props, row_prop, item.label)
        criteria, described = await resolve_filters(vocab, item.filters)
        columns.append(_Column(item.id, item.label, tuple(criteria), filters=described))
    _check_formula_graph(columns)
    return columns


def _check_series_filters(filters: list[FilterIn], base_props: set[str],
                          row_prop: str | None, label: str) -> None:
    for flt in filters:
        if flt.property in base_props:
            raise QueryError(
                f"Die Spalte '{label}' filtert auf '{flt.property}', das schon in den "
                "Grundfiltern steht. Ein Feld kann nur einmal gefiltert werden.")
        if flt.property == row_prop:
            raise QueryError(
                f"Die Spalte '{label}' filtert auf das Zeilenfeld '{flt.property}'.")


def _check_formula_graph(columns: list[_Column]) -> None:
    ids = {c.id for c in columns}
    deps = {c.id: (c.formula.names if c.formula else frozenset()) for c in columns}
    for col in columns:
        unknown = sorted(deps[col.id] - ids)
        if unknown:
            raise QueryError(
                f"Die Formel der Spalte '{col.label}' verweist auf unbekannte Spalten: "
                f"{', '.join(unknown)}.")
    state: dict[str, int] = {}

    def visit(node: str) -> None:
        if state.get(node) == 2:
            return
        if state.get(node) == 1:
            raise QueryError("Die Formeln der Spalten verweisen im Kreis aufeinander.")
        state[node] = 1
        for dep in deps[node]:
            visit(dep)
        state[node] = 2

    for col in columns:
        visit(col.id)


def _formula_order(columns: list[_Column]) -> list[_Column]:
    done: set[str] = {c.id for c in columns if not c.derived}
    pending = [c for c in columns if c.derived]
    ordered: list[_Column] = []
    while pending:
        ready = [c for c in pending if c.formula and c.formula.names <= done]
        if not ready:  # durch _check_formula_graph ausgeschlossen
            raise QueryError("Die Formeln der Spalten verweisen im Kreis aufeinander.")
        for col in ready:
            ordered.append(col)
            done.add(col.id)
            pending.remove(col)
    return ordered


def _apply_formulas(columns: list[_Column], keys: list[str]) -> None:
    for col in _formula_order(columns):
        assert col.formula is not None
        for key in keys:
            col.cells[key] = col.formula.evaluate({c.id: c.cells.get(key) for c in columns})


def _apply_formula_totals(columns: list[_Column]) -> None:
    for col in _formula_order(columns):
        assert col.formula is not None
        col.total = col.formula.evaluate({c.id: c.total for c in columns})


async def _value_rows(rows_spec, vocab: VocabStore, base, include, facet_counts,
                      data_columns: list[_Column]) -> list[_Row]:
    prop = rows_spec.property
    if include is not None:
        keys = [item["value"] for item in include]
    else:
        seen: dict[str, None] = {}
        for col in data_columns:
            for value in facet_counts[col.id]:
                seen.setdefault(value)
        keys = list(seen)
    restrict = next((set(c.values) for c in base if c.property == prop), None)
    if restrict is not None:
        keys = [k for k in keys if k in restrict]
    return [_Row(k, await vocab.label(prop, k), "value") for k in keys]


def _sort_rows(rows: list[_Row], columns: list[_Column], rows_spec, include, grouped: bool
               ) -> list[_Row]:
    if not rows_spec or not rows:
        return rows
    sort = rows_spec.sort or ("given" if grouped or include is not None else "count")
    if sort == "given":
        return rows
    if sort == "label":
        return sorted(rows, key=lambda r: (r.label.casefold(), r.key))
    first = columns[0]

    def by_count(row: _Row):
        value = first.cells.get(row.key)
        return (value is None, -(value or 0), row.label.casefold(), row.key)

    return sorted(rows, key=by_count)


async def _count_with_any_value(run: _Run, criteria: tuple[Criterion, ...], prop: str,
                                counts: dict[str, int], truncated: bool) -> int | None:
    """Zahl der Dokumente mit mindestens einem Wert im Feld; ``None``, wenn nicht bestimmbar."""
    values = tuple(counts)
    if truncated or len(values) > MAX_VALUES:
        return None
    if not values:
        return 0
    return (await run.call(criteria + (Criterion(prop, values),))).total


def _share(value: Number | None, total: Number | None) -> float | None:
    if value is None or not total:
        return None
    try:
        return round(value / total * 100, 2)
    except (OverflowError, ValueError):
        return None
