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
from dataclasses import dataclass, field
from typing import Any

from ..edusharing.client import MAX_VALUES, Aggregate, Criterion, EduSharingClient
from ..edusharing.vocab import VocabStore
from ..filters import FilterIn, resolve_filters, resolve_values
from .formula import Formula, QueryError, compile_formula
from .spec import MAX_CALLS, QuerySpec, Series

Number = int | float


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
    notes: list[str] = []
    base_list, base_described = await resolve_filters(vocab, spec.filters)
    base = tuple(base_list)
    base_props = {c.property for c in base}
    rows_spec = spec.rows
    row_prop = rows_spec.property if rows_spec else None

    # ---- Zeilenplanung (ohne Aufrufe) ----------------------------------------
    mode = "facet"
    groups: list[_Row] = []
    include: list[dict[str, str]] | None = None
    multi_valued = False
    if rows_spec:
        await vocab.check_facet(rows_spec.property)
        row_field = await vocab.field(rows_spec.property)
        multi_valued = bool(row_field and row_field.type.startswith("multivalue"))
        if rows_spec.groups:
            mode = rows_spec.group_mode
            if mode == "auto":
                mode = "distinct" if multi_valued else "sum"
            for pos, group in enumerate(rows_spec.groups, 1):
                members = await resolve_values(vocab, rows_spec.property, group.values)
                groups.append(_Row(group.id or f"g{pos}", group.label, "group",
                                   tuple(m["value"] for m in members)))
            if len({g.key for g in groups}) != len(groups):
                raise QueryError("Die IDs der Gruppen müssen eindeutig sein.")
        if rows_spec.include:
            include = await resolve_values(vocab, rows_spec.property, rows_spec.include)
        if row_prop in base_props:
            if mode == "distinct" or rows_spec.missing:
                raise QueryError(
                    f"Das Zeilenfeld '{row_prop}' steht auch in den Filtern. Das geht nicht "
                    "zusammen mit Gruppen (distinct) oder der Zeile „ohne Wert“.")
            notes.append(
                f"Das Zeilenfeld '{row_prop}' steht auch in den Filtern: Die Zeilen zeigen "
                "nur die gefilterten Werte.")

    # ---- Spaltenplanung -------------------------------------------------------
    columns = await _plan_columns(spec, vocab, run, base, base_props, row_prop, notes)
    data_columns = [c for c in columns if not c.derived]
    per_column = 1 + (1 if rows_spec and rows_spec.missing and multi_valued else 0) + (
        len(groups) if mode == "distinct" else 0)
    planned = len(data_columns) * per_column + (1 if spec.columns else 0)
    if planned > MAX_CALLS:
        raise QueryError(
            f"Die Abfrage bräuchte {planned} Einzelabfragen, erlaubt sind {MAX_CALLS}. "
            "Weniger Spalten oder Gruppen wählen.")

    # ---- Ausführung -----------------------------------------------------------
    main = await asyncio.gather(*[
        run.call(base + col.criteria, row_prop if rows_spec else None) for col in data_columns])
    facet_counts: dict[str, dict[str, int]] = {}
    truncated = False
    for col, agg in zip(data_columns, main, strict=True):
        col.total = agg.total
        if rows_spec:
            facet = agg.facets[rows_spec.property]
            facet_counts[col.id] = {v.value: v.count for v in facet.values}
            truncated = truncated or facet.truncated
    if truncated:
        notes.append(
            f"Die Werteliste von '{row_prop}' wurde vom Repositorium gekürzt; "
            "seltene Werte fehlen.")

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
                fc = facet_counts[col.id]
                for g in groups:
                    col.cells[g.key] = sum(fc.get(m, 0) for m in set(g.members))
            notes.append(
                "Gruppen sind Summen der Einzelwerte. Das ist nur exakt, wenn jedes Dokument "
                "höchstens einen Wert des Feldes trägt.")
        else:
            rows = await _value_rows(rows_spec, vocab, base, include, facet_counts, data_columns)
            for col in data_columns:
                fc = facet_counts[col.id]
                for row in rows:
                    col.cells[row.key] = fc.get(row.key, 0)

    # ---- Formeln, Sortierung, top/other, „ohne Wert" --------------------------
    _apply_formulas(columns, [r.key for r in rows])
    rows = _sort_rows(rows, columns, rows_spec, include, bool(groups))
    if rows_spec and rows_spec.top is not None and len(rows) > rows_spec.top:
        kept, rest = rows[: rows_spec.top], rows[rows_spec.top:]
        if rows_spec.other:
            if multi_valued or mode == "distinct":
                notes.append(
                    "„Übrige“ entfällt: Bei einem mehrwertigen Feld wäre die Summe der "
                    "restlichen Zeilen keine Dokumentzahl.")
            else:
                other = _Row("__other__", "Übrige", "other")
                for col in data_columns:
                    col.cells[other.key] = sum(col.cells.get(r.key) or 0 for r in rest)
                kept.append(other)
                _apply_formulas(columns, [other.key])
        notes.append(f"Angezeigt werden die ersten {rows_spec.top} von {len(rows)} Zeilen.")
        rows = kept
    if rows_spec and rows_spec.missing:
        if truncated:
            notes.append("„Ohne Wert“ entfällt, weil die Werteliste gekürzt ist.")
        else:
            missing = _Row("__missing__", "ohne Wert", "missing")
            if multi_valued:
                with_value = await asyncio.gather(*[
                    _count_with_any_value(run, base + col.criteria, rows_spec.property,
                                          facet_counts[col.id])
                    for col in data_columns])
            else:
                # Einwertiges Feld: Die Facettenwerte summieren sich zur Zahl der Dokumente
                # mit Wert. (Ein Kriterium mit mehreren Werten lehnt das Repositorium ab.)
                with_value = [sum(facet_counts[col.id].values()) for col in data_columns]
                notes.append(
                    "„Ohne Wert“ ist Gesamtzahl minus Summe der Einzelwerte; exakt, wenn "
                    "jedes Dokument höchstens einen Wert des Feldes trägt.")
            for col, counted in zip(data_columns, with_value, strict=True):
                col.cells[missing.key] = None if counted is None else col.total - counted
            if any(c is None for c in with_value):
                notes.append("„Ohne Wert“ fehlt in Spalten mit zu vielen verschiedenen Werten.")
            rows.append(missing)
            _apply_formulas(columns, [missing.key])

    _apply_formula_totals(columns)
    if any(c.derived for c in columns):
        notes.append("Spalten mit Formel sind aus anderen Spalten berechnet, nicht gezählt.")

    # ---- Ausgabe --------------------------------------------------------------
    out_rows = []
    for row in rows:
        values = [col.cells.get(row.key) for col in columns]
        entry: dict[str, Any] = {"key": row.key, "label": row.label, "kind": row.kind,
                                 "values": values}
        if spec.percent == "column":
            entry["percent"] = [_share(v, col.total) for v, col in zip(values, columns,
                                                                         strict=True)]
        elif spec.percent == "row":
            denominator = sum(v for v, col in zip(values, columns, strict=True)
                              if v is not None and not col.derived)
            entry["percent"] = [_share(v, denominator) for v in values]
        out_rows.append(entry)

    fetched = [r.fetched_at for r in run.results]
    return {
        "instance": spec.instance,
        "columns": [{"id": c.id, "label": c.label, "derived": c.derived, "total": c.total}
                    for c in columns],
        "rows": out_rows,
        "provenance": {
            "fetched_at": max(fetched) if fetched else None,
            "oldest_fetched_at": min(fetched) if fetched else None,
            "cached": bool(run.results) and all(r.cached for r in run.results),
            "queries": len(run.results),
            "notes": notes,
            "filters": base_described,
            "columns": [{"id": c.id, "filters": c.filters,
                         "formula": c.formula.text if c.formula else None} for c in columns],
        },
    }


async def _plan_columns(
    spec: QuerySpec, vocab: VocabStore, run: _Run, base: tuple[Criterion, ...] | list[Criterion],
    base_props: set[str], row_prop: str | None, notes: list[str],
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
                notes.append(
                    f"Spalten: die {len(top)} häufigsten Werte von '{prop}'; weitere "
                    "Werte sind nicht dargestellt.")
        return [_Column(f"c{pos}", item["label"],
                        (Criterion(prop, (item["value"],)),),
                        filters=[{"property": prop, "values": [item]}])
                for pos, item in enumerate(chosen, 1)]
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
                                counts: dict[str, int]) -> int | None:
    """Zahl der Dokumente mit mindestens einem Wert im Feld; ``None``, wenn nicht bestimmbar."""
    values = tuple(counts)
    if not values:
        return 0
    if len(values) > MAX_VALUES:
        return None
    return (await run.call(criteria + (Criterion(prop, values),))).total


def _share(value: Number | None, total: Number | None) -> float | None:
    if value is None or not total:
        return None
    return round(value / total * 100, 2)
