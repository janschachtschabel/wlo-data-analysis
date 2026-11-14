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
import json
import re
from dataclasses import dataclass, field
from typing import Any

from ..edusharing.client import MAX_VALUES, Aggregate, Criterion, EduSharingClient
from ..edusharing.errors import EduSharingError
from ..edusharing.vocab import VocabStore
from ..errors import Notes, QueryError
from ..filters import (
    FilterIn,
    check_prefix_shape,
    check_prefix_values,
    resolve_filters,
    resolve_values,
)
from ..snapshot.analyses import run_analysis
from .formula import Formula, compile_formula
from .spec import MAX_CALLS, MAX_GROUPS, MAX_SERIES, QuerySpec, Series

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
    unused: bool = False  # aus der Werteliste ergänzt, kein Material trägt den Wert


class _Run:
    """Zählt und bündelt die Aggregat-Aufrufe einer Abfrage."""

    def __init__(self, client: EduSharingClient, content_type: str, refresh: bool) -> None:
        self._client = client
        self._content_type = content_type
        self._refresh = refresh
        self._tasks: dict[tuple, asyncio.Task[Aggregate]] = {}
        self.results: list[Aggregate] = []

    def call(self, criteria: tuple[Criterion, ...], facet: str | None = None,
             facet_limit: int | None = None) -> asyncio.Task[Aggregate]:
        key = (tuple(sorted((c.property, tuple(sorted(c.values))) for c in criteria)), facet,
               facet_limit)
        if key not in self._tasks:
            self._tasks[key] = asyncio.ensure_future(self._do(criteria, facet, facet_limit))
        return self._tasks[key]

    async def _do(self, criteria: tuple[Criterion, ...], facet: str | None,
                  facet_limit: int | None = None) -> Aggregate:
        more = {"facet_limit": facet_limit} if facet_limit else {}
        result = await self._client.aggregate(
            criteria, [facet] if facet else [], content_type=self._content_type,
            refresh=self._refresh, **more)
        self.results.append(result)
        return result

    async def cancel(self) -> None:
        for task in self._tasks.values():
            task.cancel()
        await asyncio.gather(*self._tasks.values(), return_exceptions=True)


async def run_query(spec: QuerySpec, client: EduSharingClient, vocab: VocabStore,
                    snapshot: Any = None) -> dict[str, Any]:
    if spec.snapshot is not None:
        # aus dem lokalen Abzug, ohne Anfrage an das Repositorium
        if snapshot is None:
            raise QueryError("Der lokale Abzug steht hier nicht zur Verfügung.")
        return await asyncio.to_thread(run_analysis, snapshot, spec, client.instance)
    run = _Run(client, spec.content_type, spec.refresh)
    try:
        result = await _execute(spec, client, vocab, run)
    except BaseException:
        await run.cancel()
        raise
    return _gaps(result, spec) if spec.gaps is not None else result


def _gaps(result: dict[str, Any], spec: QuerySpec) -> dict[str, Any]:
    """Aus der Matrix wird eine Liste: je Feld die Zahl, die Erwartung aus Zeilen- und
    Spaltensumme (wenn Zeile und Spalte unabhängig wären) und das Verhältnis. Nur Felder
    unter der Erwartung, die kleinsten zuerst."""
    ask = spec.gaps
    assert ask is not None
    cols = [(n, c) for n, c in enumerate(result["columns"])
            if not c.get("derived") and not str(c["id"]).startswith("__")]
    rows = [r for r in result["rows"] if r.get("kind") in ("value", "group")]
    cells = [[r["values"][n] for n, _ in cols] for r in rows]
    # ein nicht bestimmbares Feld zählt nicht als null; es bleibt aus Summen und Liste
    unknown = sum(1 for line in cells for value in line if value is None)
    row_sums = [sum(v or 0 for v in line) for line in cells]
    col_sums = [sum(line[k] or 0 for line in cells) for k in range(len(cols))]
    whole = sum(row_sums)
    found = []
    for i, row in enumerate(rows):
        for k, (_, col) in enumerate(cols):
            value = cells[i][k]
            expected = row_sums[i] * col_sums[k] / whole if whole else 0
            if value is None or expected < ask.min_expected or value >= expected * ask.below / 100:
                continue
            found.append((value / expected, -expected, {
                "key": json.dumps([row["key"], col["id"]], ensure_ascii=False),
                "label": f"{row['label']} · {col['label']}", "kind": "value",
                "values": [value, round(expected), round(100 * value / expected, 1)]}))
    found.sort(key=lambda item: item[:2])
    columns = [
        {"id": "count", "label": "Materialien", "derived": False, "total": None},
        {"id": "expected", "label": "erwartet", "derived": True, "total": None},
        {"id": "ratio", "label": "Anteil am Erwarteten (%)", "derived": True, "total": None}]
    shown = found[:ask.limit]
    text = (
        f"Lückenliste aus {len(rows)} Zeilen × {len(cols)} Spalten: {len(shown)} von "
        f"{len(found)} Feldern unter {ask.below:g} % der Erwartung, die mit dem kleinsten "
        "Anteil zuerst. "
        "Erwartet ist, was Zeilen- und Spaltensumme der gezeigten Matrix ergäben, wenn Zeile "
        "und Spalte unabhängig wären; Felder mit einer Erwartung unter "
        f"{ask.min_expected:g} bleiben weg. Materialien mit mehreren Werten zählen in den "
        "Summen mehrfach. Eine Lücke kann fachlich begründet sein.")
    if unknown:
        text += f" {unknown} Felder ließen sich nicht bestimmen und fehlen."
    notes = [*result["provenance"]["notes"], {"code": "gaps", "text": text}]
    return {**result, "columns": columns, "rows": [item[2] for item in shown],
            "total_row": False,
            "provenance": {**result["provenance"], "notes": notes,
                           "columns": [{"id": c["id"], "filters": [], "formula": None}
                                       for c in columns]}}


async def _execute(spec: QuerySpec, client: EduSharingClient, vocab: VocabStore, run: _Run
                   ) -> dict[str, Any]:
    if spec.fill is not None:
        return await _execute_fill(spec, client, vocab, run)
    if spec.vocab_check is not None:
        return await _execute_vocab_check(spec, client, vocab, run)
    if spec.profile is not None:
        return await _execute_profile(spec, client, vocab, run)
    if spec.sizes is not None:
        return await _execute_sizes(spec, client, vocab, run)
    if spec.topics is not None:
        from .topics import execute_topics  # hier, weil das Modul die Helfer von hier nutzt
        return await execute_topics(spec, client, vocab, run)
    notes = Notes()
    prefix_fields = set(client.instance.prefix_fields)
    base_list, base_described = await resolve_filters(vocab, spec.filters)
    base = tuple(base_list)
    base_props = {c.property for c in base}
    rows_spec = spec.rows
    row_prop = rows_spec.property if rows_spec else None
    # Einwertiges Feld: Facettenwerte summieren sich exakt; ein Kriterium nimmt nur einen Wert.
    single = row_prop in prefix_fields
    # Laut Metadatenset höchstens ein Wert je Material: Die Werte summieren sich ebenfalls,
    # auch wenn das Repositorium das Feld nicht als Filter zählt.
    row_field = await vocab.field(row_prop) if row_prop else None
    one_valued = single or (row_field is not None and row_field.type in SINGLE_TYPES)

    # ---- Zeilenplanung (ohne Aufrufe) ----------------------------------------
    mode = "facet"
    groups: list[_Row] = []
    include: list[dict[str, str]] | None = None
    include_map: list[str] | None = None
    if rows_spec:
        await vocab.check_facet(rows_spec.property)
        if rows_spec.vocab:
            await known_values(vocab, rows_spec.property)  # vor dem ersten Aufruf prüfen
        if rows_spec.rollup:
            mode = "distinct"
            if single:
                raise QueryError(f"'{row_prop}' ist einwertig und hat keinen Baum.")
            groups = await _rollup_rows(vocab, rows_spec, notes)
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
    planned = n_planned * per_column + (2 if spec.columns else 0) + len(prefix_fields)
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

    row_totals: dict[str, int] = {}
    probed: list[bool] = []

    async def exact_criterion() -> bool:
        """Vergleicht das Kriterium des Zeilenfelds genau? Einmal je Abfrage geprüft, an der
        ersten Spalte; das Verhalten hängt am Feld, nicht an den Filtern der Spalte."""
        if not probed:
            col = data_columns[0]
            probed.append(await _criterion_exact(run, base + col.criteria, rows_spec.property,
                                                 facet_counts[col.id]))
        return probed[0]

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
            ok = await exact_criterion()
            for col in data_columns:
                for g in groups:
                    counted = next(it).total
                    parts = [facet_counts[col.id].get(m, 0) for m in g.members]
                    fits = ok and (col.id in cut or max(parts) <= counted <= sum(parts))
                    col.cells[g.key] = counted if fits else None
            if any(col.cells[g.key] is None for col in data_columns for g in groups):
                notes.add("groups_unknown",
                          "Einige Gruppen sind nicht bestimmbar: Das Repositorium zählt das "
                          "Feld nicht genau als Filter.")
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
            if spec.columns is not None:
                # Die Zeilen einer Matrix sind alle vorkommenden Werte, nicht nur die mit
                # Treffern in den gezeigten Spalten: Eine leere Zeile ist die größte Lücke.
                whole = (await run.call(base, row_prop)).facets[row_prop]
                row_totals = {v.value: v.count for v in whole.values}
            rows = await _value_rows(rows_spec, vocab, base, include,
                                     {**facet_counts, "": row_totals}, data_columns)
            if rows_spec.match or rows_spec.vocab:
                rows = await _select_rows(rows, rows_spec, vocab, notes,
                                          _row_filter(base, row_prop))
            for row in rows:
                row.members = (row.key,)
                for col in data_columns:
                    col.cells[row.key] = cell(col, row.key)
            if not one_valued and len(rows) > 1:
                notes.add("rows_overlap", 
                    f"Ein Dokument kann mehrere Werte von '{row_prop}' tragen und steht dann "
                    "in mehreren Zeilen; die Zeilen summieren sich nicht zur Gesamtzahl.")

        if groups:
            grouped = {m for g in groups for m in g.members}
            loose = sorted({v for fc in facet_counts.values() for v, n in fc.items() if n
                            and (not rows_spec.match or v.startswith(rows_spec.match))}
                           - grouped)
            if loose:
                shown = ", ".join(f"'{v}'" if v else "(leer)" for v in loose[:5])
                notes.add("values_ungrouped", 
                    (f"{len(loose)} vorkommende Werte von '{row_prop}' stehen"
                     if len(loose) > 1 else f"Ein vorkommender Wert von '{row_prop}' steht")
                    + f" in keiner Gruppe und {'sind' if len(loose) > 1 else 'ist'} nicht "
                    f"dargestellt ({shown}{' …' if len(loose) > 5 else ''}). "
                    "Die Zeilen summieren sich deshalb nicht zur Gesamtzahl.")

    # ---- Formeln, Sortierung, top/other, „ohne Wert" --------------------------
    _apply_formulas(columns, [r.key for r in rows])
    rows = _sort_rows(rows, columns, rows_spec, include, bool(groups),
                      across=spec.columns is not None, row_totals=row_totals)
    if rows_spec and rows_spec.top is not None and len(rows) > rows_spec.top:
        kept, rest = rows[: rows_spec.top], rows[rows_spec.top:]
        _unused_after_top(notes, rows, kept)
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
            elif one_valued:
                for col in data_columns:
                    col.cells[other.key] = _sum(cell(col, v) for v in rest_values)
            elif len(rest_values) > MAX_VALUES:
                other = None
                notes.add("other_dropped", "„Übrige“ entfällt: zu viele verschiedene Werte.")
            else:
                counted = await asyncio.gather(*[
                    run.call(base + col.criteria + (Criterion(rows_spec.property, rest_values),))
                    for col in data_columns])
                ok = await exact_criterion()
                for col, agg in zip(data_columns, counted, strict=True):
                    parts = [facet_counts[col.id].get(v, 0) for v in rest_values]
                    fits = ok and (col.id in cut or max(parts) <= agg.total <= sum(parts))
                    col.cells[other.key] = agg.total if fits else None
                if any(col.cells[other.key] is None for col in data_columns):
                    notes.add("other_unknown",
                              "„Übrige“ ist nicht bestimmbar: Das Repositorium zählt das "
                              "Feld nicht genau als Filter.")
                notes.add("other_overlap", 
                    "„Übrige“ zählt die Dokumente mit mindestens einem der nicht gezeigten "
                    "Werte; ein Dokument kann zugleich in einer gezeigten Zeile stehen.")
            if other is not None:
                kept.append(other)
                _apply_formulas(columns, [other.key])
        rows = kept
    if rows_spec and rows_spec.missing:
        missing = _Row("__missing__", "ohne Wert", "missing")
        if one_valued:
            # Einwertig: Die Facettenwerte summieren sich zur Zahl der Dokumente mit Wert.
            with_value: list[int | None] = [
                None if col.id in cut else sum(
                    n for value, n in facet_counts[col.id].items() if value.strip())
                for col in data_columns]
            if not single:
                # Einwertig nur laut Metadatenset: Übersteigt die Summe die Gesamtzahl,
                # tragen Materialien doch mehrere Werte.
                with_value = [None if n is not None and col.total is not None
                              and n > col.total else n
                              for n, col in zip(with_value, data_columns, strict=True)]
                notes.add("missing_summed",
                          "„Ohne Wert“ ist die Gesamtzahl minus der Summe der Werte. Das "
                          "stimmt, solange jedes Material höchstens einen Wert trägt (laut "
                          "Metadatenset einwertig); sonst ist die Zahl zu niedrig.")
        elif not await exact_criterion():
            with_value = [None] * len(data_columns)
        else:
            with_value = await asyncio.gather(*[
                _count_with_any_value(run, base + col.criteria, rows_spec.property,
                                      facet_counts[col.id], col.id in cut, col.total)
                for col in data_columns])
        for col, counted in zip(data_columns, with_value, strict=True):
            col.cells[missing.key] = (None if counted is None or col.total is None
                                      else max(col.total - counted, 0))
        if any(c is None for c in with_value):
            notes.add("missing_unknown", 
                "„Ohne Wert“ fehlt, wo die Werteliste gekürzt ist, zu viele Werte hat oder "
                "das Repositorium das Feld nicht oder nicht genau als Filter zählt.")
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

    hidden = {s.id for s in spec.series or [] if s.hidden}
    if hidden:
        keep = [n for n, col in enumerate(columns) if col.id not in hidden]
        columns = [columns[n] for n in keep]
        for entry in out_rows:
            for name in ("values", "percent"):
                if name in entry:
                    entry[name] = [entry[name][n] for n in keep]

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


def _rate(filled: Number | None, total: Number | None) -> float | None:
    if filled is None or not total:
        return None
    return round(100 * filled / total, 1)


def _field_id(prop: str) -> str:
    """Spalten-ID aus dem Feldnamen: gleich über alle Läufe."""
    return (re.sub(r"[^a-z0-9]+", "_", prop.casefold()).strip("_") or "feld")[:40]


async def _execute_vocab_check(spec: QuerySpec, client: EduSharingClient, vocab: VocabStore,
                               run: _Run) -> dict[str, Any]:
    """Je Feld: wie viele der vorkommenden Werte stehen nicht im Vokabular des Feldes, und
    wie viele Zuordnungen betrifft das. Ein Aufruf je Feld."""
    check = spec.vocab_check
    assert check is not None
    notes = Notes()
    prefix_fields = set(client.instance.prefix_fields)
    base_list, base_described = await resolve_filters(vocab, spec.filters)
    base = tuple(base_list)
    base_props = {c.property for c in base}
    check_prefix_shape(base, prefix_fields)

    async def occurring(prop: str):
        return (await run.call((), prop)).facets[prop]

    await check_prefix_values(occurring, list(base), prefix_fields, notes)
    known: dict[str, set[str]] = {}
    labels: dict[str, str] = {}
    for item in check.fields:  # erst alles prüfen, dann fragen
        if item.property in base_props:
            raise QueryError(
                f"'{item.property}' steht auch in den Filtern; die Facette ignoriert den "
                "Filter auf dem eigenen Feld. Bitte eines von beiden weglassen.")
        field = await vocab.field(item.property)
        if field is not None and field.values:
            known[item.property] = {v.id for v in field.values}
        labels[item.property] = item.label or (field.caption if field and field.caption
                                               else item.property)
    main_task = run.call(base)

    if not known:
        raise QueryError(
            "Keines der Felder hat auf dieser Instanz eine Werteliste; ein Abgleich mit dem "
            "Vokabular geht nur bei Feldern mit Werteliste.")

    async def one(prop: str):
        if prop not in known:  # ohne Werteliste wird nichts gefragt
            return None
        try:
            return (await run.call(base, prop)).facets[prop]
        except EduSharingError as exc:
            if not _refused(exc):
                raise
            await main_task
            return None

    facets = await asyncio.gather(*[one(f.property) for f in check.fields])
    main = await main_task
    columns = [
        {"id": "values", "label": "verschiedene Werte", "derived": False, "total": None},
        {"id": "unknown_values", "label": "davon nicht im Vokabular", "derived": False,
         "total": None},
        {"id": "uses", "label": "Zuordnungen", "derived": False, "total": None},
        {"id": "unknown_uses", "label": "davon mit unbekanntem Wert", "derived": False,
         "total": None},
        {"id": "share", "label": "Anteil %", "derived": True, "total": None}]
    out_rows = []
    refused, cut, closed = [], [], []
    for item, facet in zip(check.fields, facets, strict=True):
        label = labels[item.property]
        if facet is None:
            (refused if item.property in known else closed).append(label)
            values: list[Number | None] = [None] * 5
        else:
            if facet.truncated:
                cut.append(label)
            outside = [v for v in facet.values if v.value not in known[item.property]]
            uses, bad = sum(v.count for v in facet.values), sum(v.count for v in outside)
            values = [len(facet.values), len(outside), uses, bad,
                      round(100 * bad / uses, 1) if uses else None]
        out_rows.append({"key": item.property, "label": label, "kind": "value",
                         "values": values})
    notes.add("vocab_check",
              "Verglichen werden die vorkommenden Werte mit der Werteliste des Feldes im "
              "Metadatenset. „Zuordnungen“ zählt je Wert die Materialien; ein Material mit "
              "mehreren Werten zählt mehrfach.")
    if closed:
        notes.add("vocab_closed",
                  f"Nicht bestimmbar: {', '.join(closed)} (auf dieser Instanz ohne "
                  "Werteliste; es wird nichts abgefragt).")
    if refused:
        notes.add("vocab_refused",
                  f"Nicht bestimmbar: {', '.join(refused)} (das Repositorium zählt dieses "
                  "Feld nicht).")
    if cut:
        notes.add("facet_truncated",
                  f"Die Werteliste von {', '.join(cut)} wurde vom Repositorium gekürzt; "
                  "seltene Werte fehlen in der Zählung.")
    fetched = [r.fetched_at for r in run.results]
    inst = client.instance
    return {
        "schema_version": SCHEMA_VERSION, "instance": spec.instance,
        "columns": columns, "rows": out_rows, "total_row": False, "base_total": main.total,
        "provenance": {
            "fetched_at": max(fetched) if fetched else None,
            "oldest_fetched_at": min(fetched) if fetched else None,
            "cached": bool(run.results) and all(r.cached for r in run.results),
            "queries": len(run.results), "notes": list(notes),
            "instance": {"id": inst.id, "base_url": inst.base_url, "repository": inst.repository,
                         "metadataset": inst.metadataset, "query": inst.query},
            "spec": spec.model_dump(mode="json", exclude={"refresh"}),
            "rows": None, "filters": base_described,
            "columns": [{"id": c["id"], "filters": [], "formula": None} for c in columns],
        },
    }


def _result(spec: QuerySpec, client: EduSharingClient, run: _Run, notes: Notes,
            columns: list[dict[str, Any]], rows: list[dict[str, Any]],
            base_described: list, provenance_rows: dict[str, Any] | None) -> dict[str, Any]:
    fetched = [r.fetched_at for r in run.results]
    inst = client.instance
    return {
        "schema_version": SCHEMA_VERSION, "instance": spec.instance,
        "columns": columns, "rows": rows,
        "provenance": {
            "fetched_at": max(fetched) if fetched else None,
            "oldest_fetched_at": min(fetched) if fetched else None,
            "cached": bool(run.results) and all(r.cached for r in run.results),
            "queries": len(run.results), "notes": list(notes),
            "instance": {"id": inst.id, "base_url": inst.base_url, "repository": inst.repository,
                         "metadataset": inst.metadataset, "query": inst.query},
            "spec": spec.model_dump(mode="json", exclude={"refresh"}),
            "rows": provenance_rows, "filters": base_described,
            "columns": [{"id": c["id"], "filters": [], "formula": None} for c in columns],
        },
    }


async def _execute_profile(spec: QuerySpec, client: EduSharingClient, vocab: VocabStore,
                           run: _Run) -> dict[str, Any]:
    """Ausprägungen eines Merkmals je Zeile. Je Zeilenwert eine Abfrage: der Wert als
    Kriterium, das Merkmal als Facette. Zählt das Kriterium etwas anderes als die Facette
    des Zeilenfelds (Präfix, andere Schreibweise), bleibt die Zeile unbekannt."""
    profile, rows_spec = spec.profile, spec.rows
    assert profile is not None and rows_spec is not None
    notes = Notes()
    prefix_fields = set(client.instance.prefix_fields)
    base_list, base_described = await resolve_filters(vocab, spec.filters)
    base = tuple(base_list)
    base_props = {c.property for c in base}
    row_prop, prop = rows_spec.property, profile.property
    if row_prop == prop:
        raise QueryError("Zeilen und Merkmal brauchen verschiedene Felder.")
    # Mit einer festen Auswahl der Zeilen darf deren Feld auch gefiltert sein: Gezeigt wird
    # die Schnittmenge, und die Summen gelten für den gefilterten Bestand.
    narrowed = row_prop in base_props and bool(rows_spec.include)
    for name in (row_prop, prop):
        if name in base_props and not (narrowed and name == row_prop):
            raise QueryError(
                f"'{name}' steht auch in den Filtern; die Facette ignoriert den Filter auf "
                "dem eigenen Feld. Bitte eines von beiden weglassen.")
        if _helper(name):
            raise QueryError(f"'{name}' ist eine Suchhilfe des Repositoriums, kein "
                             "gespeichertes Feld.")
    await vocab.check_facet(row_prop)
    await vocab.check_facet(prop)
    include = None
    if rows_spec.include:
        include = await resolve_values(vocab, row_prop, rows_spec.include)
    check_prefix_shape(base, prefix_fields)

    async def occurring(name: str):
        return (await run.call((), name)).facets[name]

    await check_prefix_values(occurring, list(base), prefix_fields, notes)
    main, overall = await asyncio.gather(run.call(base, row_prop), run.call(base, prop))
    row_facet, facet = main.facets[row_prop], overall.facets[prop]
    counts = {v.value: v.count for v in row_facet.values}
    if row_facet.truncated:
        notes.add("facet_truncated",
                  f"Die Werteliste von '{row_prop}' wurde vom Repositorium gekürzt; "
                  "seltene Werte fehlen.")
    keys = [i["value"] for i in include] if include else [k for k in counts if k.strip()]
    per_row_base = base
    if narrowed:
        allowed = {v for c in base if c.property == row_prop for v in c.values}
        keys = [k for k in keys if k in allowed]
        # ein Material mit dem Wert der Zeile erfüllt den Filter bereits
        per_row_base = tuple(c for c in base if c.property != row_prop)
        notes.add("rows_filtered",
                  f"Das Zeilenfeld '{row_prop}' steht auch in den Filtern: Die Zeilen zeigen "
                  "nur die gefilterten Werte.")
    rows = [_Row(k, await vocab.label(row_prop, k), "value") for k in keys]
    if rows_spec.match:
        rows = await _select_rows(rows, rows_spec, vocab, notes)
    count_column = _Column("count", "Anzahl")
    for row in rows:
        count_column.cells[row.key] = counts.get(row.key, None if row_facet.truncated else 0)
    rows = _sort_rows(rows, [count_column], rows_spec, include, False)
    if rows_spec.top is not None and len(rows) > rows_spec.top:
        notes.add("rows_top",
                  f"Angezeigt werden die ersten {rows_spec.top} von {len(rows)} Zeilen.")
        rows = rows[: rows_spec.top]

    offered = sorted((v for v in facet.values if v.value.strip()),
                     key=lambda v: (-v.count, v.value))
    chosen = offered[: profile.top]
    if len(offered) > len(chosen) or facet.truncated:
        notes.add("columns_top",
                  f"Spalten: die {len(chosen)} häufigsten Werte von '{prop}'; weitere Werte "
                  "sind nicht dargestellt.")
    field = await vocab.field(prop)
    one_valued = prop in prefix_fields or (field is not None and field.type in SINGLE_TYPES)
    with_missing = profile.missing and one_valued
    if profile.missing and not one_valued:
        notes.add("profile_multi",
                  f"Ein Material kann mehrere Werte von '{prop}' tragen: Die Spalten "
                  "überschneiden sich, und „ohne Angabe“ ist so nicht bestimmbar.")

    refused: set[str] = set()

    async def one(row: _Row) -> Aggregate | None:
        if not counts.get(row.key):
            return None
        try:
            return await run.call(per_row_base + (Criterion(row_prop, (row.key,)),), prop)
        except EduSharingError as exc:
            if not _refused(exc):
                raise
            refused.add(row.key)
            return None

    per_row = await asyncio.gather(*[one(row) for row in rows])
    share = profile.show == "share"

    def shown(value: int | None, total: int | None) -> Number | None:
        return _share(value, total) if share else value

    # „(%)“ statt „%“: Anteile eines Merkmals sind kein Füllgrad, dessen Färbung passt nicht
    suffix = " (%)" if share else ""
    columns: list[dict[str, Any]] = [
        {"id": "count", "label": "Anzahl", "derived": False, "total": main.total}]
    for v in chosen:
        # ein Wert, der wie eine feste Spalte heißt, bekommt eine eigene ID
        col_id = "v:" + v.value if v.value in ("count", "__missing__") else v.value
        columns.append({"id": col_id, "label": await vocab.label(prop, v.value) + suffix,
                        "derived": share, "total": shown(v.count, main.total)})
    if with_missing:
        filled = sum(v.count for v in offered)
        columns.append({"id": "__missing__", "label": "ohne Angabe" + suffix, "derived": True,
                        "total": shown(main.total - filled, main.total)
                        if 0 <= filled <= main.total and not facet.truncated else None})
    out_rows, inexact, cut_rows, refused_rows = [], [], [], []
    for row, agg in zip(rows, per_row, strict=True):
        total = count_column.cells[row.key]
        values: list[Number | None] = [total]
        if total == 0:
            values += [None if share else 0] * (len(columns) - 1)
        elif total is None:  # die Werteliste der Zeilen ist gekürzt, der Wert fehlt darin
            cut_rows.append(row.label)
            values += [None] * (len(columns) - 1)
        elif row.key in refused:
            refused_rows.append(row.label)
            values += [None] * (len(columns) - 1)
        elif agg is None or agg.total != total:
            # das Kriterium zählt nicht dieselben Materialien wie die Facette der Zeile
            inexact.append(row.label)
            values += [None] * (len(columns) - 1)
        else:
            got = agg.facets[prop]
            found = {v.value: v.count for v in got.values}
            values += [shown(found.get(v.value, None if got.truncated else 0), total)
                       for v in chosen]
            if with_missing:
                filled = sum(n for value, n in found.items() if value.strip())
                values.append(shown(total - filled, total)
                              if filled <= total and not got.truncated else None)
        out_rows.append({"key": row.key, "label": row.label, "kind": "value", "values": values})
    if inexact:
        shown_rows = ", ".join(inexact[:5]) + (" …" if len(inexact) > 5 else "")
        notes.add("profile_inexact",
                  f"Nicht bestimmbar: {shown_rows}. Das Repositorium zählt den Wert als "
                  "Filter anders als in der Werteliste (etwa als Wortanfang).")
    if cut_rows:
        notes.add("profile_cut",
                  f"Nicht bestimmbar: {', '.join(cut_rows[:5])}{' …' if len(cut_rows) > 5 else ''}"
                  ". Der Wert fehlt in der gekürzten Werteliste der Zeilen.")
    if refused_rows:
        notes.add("profile_refused",
                  f"Nicht bestimmbar: {', '.join(refused_rows[:5])}"
                  f"{' …' if len(refused_rows) > 5 else ''}. Das Repositorium zählt "
                  f"'{row_prop}' nicht als Filter.")
    notes.add("profile",
              ("Anteile beziehen sich auf die Materialien der Zeile. " if share else "")
              + "Je Zeile eine eigene Abfrage; „ohne Angabe“ ist die Anzahl der Zeile minus "
              "der Summe der Werte. Das stimmt, solange jedes Material höchstens einen Wert "
              "trägt (laut Metadatenset einwertig); sonst ist die Zahl zu niedrig."
              if with_missing else
              ("Anteile beziehen sich auf die Materialien der Zeile. " if share else "")
              + "Je Zeile eine eigene Abfrage.")
    return _result(spec, client, run, notes, columns, out_rows, base_described,
                   {"property": row_prop, "mode": "facet",
                    "include": [i["value"] for i in include] if include else None,
                    "groups": []})


def _thousands(n: int) -> str:
    return f"{n:,}".replace(",", ".")


async def _execute_sizes(spec: QuerySpec, client: EduSharingClient, vocab: VocabStore,
                         run: _Run) -> dict[str, Any]:
    """Größenklassen eines Feldes: eine Abfrage, die Werteliste wird nach der Zahl der
    Materialien je Wert in Klassen gezählt. Einzelne Werte erscheinen nicht."""
    sizes = spec.sizes
    assert sizes is not None
    notes = Notes()
    prefix_fields = set(client.instance.prefix_fields)
    base_list, base_described = await resolve_filters(vocab, spec.filters)
    base = tuple(base_list)
    prop = sizes.property
    if prop in {c.property for c in base}:
        raise QueryError(
            f"'{prop}' steht auch in den Filtern; die Facette ignoriert den Filter auf dem "
            "eigenen Feld. Bitte eines von beiden weglassen.")
    if _helper(prop):
        raise QueryError(f"'{prop}' ist eine Suchhilfe des Repositoriums, kein gespeichertes "
                         "Feld.")
    await vocab.check_facet(prop)
    check_prefix_shape(base, prefix_fields)

    async def occurring(name: str):
        return (await run.call((), name)).facets[name]

    await check_prefix_values(occurring, list(base), prefix_fields, notes)
    facet = (await run.call(base, prop)).facets[prop]
    counted = [v.count for v in facet.values if v.value.strip() and v.count > 0]
    n_values, n_uses = len(counted), sum(counted)
    bounds = sizes.bounds
    cut = facet.truncated
    out_rows = []
    for pos, low in enumerate(bounds):
        high = bounds[pos + 1] - 1 if pos + 1 < len(bounds) else None
        inside = [n for n in counted if n >= low and (high is None or n <= high)]
        if high is None:
            key, label = f"{low}+", f"ab {_thousands(low)}"
        elif high == low:
            key, label = str(low), _thousands(low)
        else:
            key, label = f"{low}-{high}", f"{_thousands(low)} bis {_thousands(high)}"
        # gekürzte Werteliste: Anteile am sichtbaren Teil wären falsch
        out_rows.append({"key": key, "label": label, "kind": "value",
                         "values": [len(inside),
                                    None if cut else _share(len(inside), n_values),
                                    sum(inside), None if cut else _share(sum(inside), n_uses)]})
    columns = [
        {"id": "values", "label": "verschiedene Werte", "derived": False,
         "total": None if cut else n_values},
        {"id": "values_share", "label": "Anteil der Werte %", "derived": True, "total": None},
        {"id": "uses", "label": "Materialien", "derived": False,
         "total": n_uses + facet.other_count},
        {"id": "uses_share", "label": "Anteil der Materialien %", "derived": True,
         "total": None}]
    field = await vocab.field(prop)
    caption = field.caption if field and field.caption else prop
    notes.add("sizes",
              f"Jede Zeile ist eine Größenklasse: Werte von „{caption}“ mit so vielen "
              "Materialien. Ein Material mit mehreren Werten zählt bei jedem davon.")
    if facet.truncated:
        notes.add("facet_truncated",
                  f"Die Werteliste von '{prop}' wurde vom Repositorium gekürzt; seltene "
                  "Werte fehlen. Die kleinen Klassen sind zu niedrig, die Zahl der Werte "
                  "und die Anteile sind deshalb nicht angegeben.")
    return _result(spec, client, run, notes, columns, out_rows, base_described, None)


# Ablehnungen des Repositoriums, die nur das eine Feld betreffen
def _refused(exc: EduSharingError) -> bool:
    """Das Repositorium kennt das Feld nicht oder lehnt die Abfrage als ungültig ab (400).
    Zugriffsfehler (401, 403, 404) betreffen die Instanz, nicht das Feld."""
    return exc.kind == "unknown_property" or (exc.kind == "rejected" and exc.status == 400)


# Suchhilfen des Repositoriums: keine gespeicherten Felder, ihre Facette ist immer leer
def _helper(prop: str) -> bool:
    return prop.startswith("virtual:") or prop == "license"
# Widget-Arten, bei denen ein Material höchstens einen Wert trägt
SINGLE_TYPES = frozenset({"singleoption", "radioHorizontal", "radioVertical",
                          "singlevalueTree", "slider", "checkbox"})


async def _execute_fill(spec: QuerySpec, client: EduSharingClient, vocab: VocabStore,
                        run: _Run) -> dict[str, Any]:
    """Füllgrade. Je Feld wird gezählt, wie viele Dokumente dort mindestens einen Wert haben:
    bei einem Präfix-Feld mit dem leeren Wert als Kriterium, sonst mit allen vorkommenden
    Werten als ein Kriterium. „Ohne Wert“ ist die Differenz zur Gesamtzahl."""
    fill = spec.fill
    assert fill is not None
    notes = Notes()
    prefix_fields = set(client.instance.prefix_fields)
    base_list, base_described = await resolve_filters(vocab, spec.filters)
    base = tuple(base_list)
    base_props = {c.property for c in base}
    rows_spec = spec.rows
    row_prop = rows_spec.property if rows_spec else None
    if rows_spec:
        if rows_spec.groups or rows_spec.other or rows_spec.missing:
            raise QueryError(
                "Füllgrade je Zeile gehen nicht zusammen mit Gruppen, „Übrige“ oder der "
                "Zeile „ohne Wert“.")
        await vocab.check_facet(rows_spec.property)
        if rows_spec.vocab:
            await known_values(vocab, rows_spec.property)
        if row_prop in base_props:
            # Die Facette ignoriert den Filter auf dem eigenen Feld.
            notes.add("rows_filtered",
                      f"Das Zeilenfeld '{row_prop}' steht auch in den Filtern: Die Zeilen "
                      "zeigen nur die gefilterten Werte.")
    # Erst alles prüfen, dann fragen: Kein Aufruf für ein Feld, das nicht freigegeben ist.
    # Ein solches Feld bleibt unbekannt, damit eine gespeicherte Definition auch auf einer
    # Instanz läuft, die nicht jedes ihrer Felder kennt.
    closed: set[str] = set()
    for item in fill.fields:
        if item.property == row_prop:
            raise QueryError(
                f"'{item.property}' ist das Zeilenfeld; dort hat jede Zeile einen Wert.")
        try:
            await vocab.check_facet(item.property)
        except EduSharingError as exc:
            if exc.kind != "invalid_query":
                raise
            closed.add(item.property)
    ids = [_field_id(f.property) for f in fill.fields]
    if rows_spec and (len(set(ids)) != len(ids) or "count" in ids):
        raise QueryError(
            "Zwei der Felder ergeben dieselbe Spalte (ihre Namen unterscheiden sich nur in "
            "Sonderzeichen oder erst nach 40 Zeichen, oder eines heißt wie die Spalte "
            "„Anzahl“).")
    check_prefix_shape(base, prefix_fields)

    async def occurring(prop: str):
        return (await run.call((), prop)).facets[prop]

    await check_prefix_values(occurring, list(base), prefix_fields, notes)

    main_task = run.call(base, row_prop)
    reasons: dict[str, str] = {}   # Feld → warum nicht bestimmbar
    ranges: dict[str, tuple[int, int]] = {}
    summed: set[str] = set()

    async def measure(prop: str) -> Aggregate | int | bool | None:
        """Materialien mit Wert: eine Zählung (mit Zeilenfacette), eine Zahl (nur insgesamt
        bekannt), ``True``: alle, ``False``: keines, ``None``: nicht bestimmbar."""
        if prop in closed:
            reasons[prop] = "closed"
            return None
        if prop in base_props:
            return True
        if prop in prefix_fields:
            return await run.call(base + (Criterion(prop, ("",)),), row_prop)
        if _helper(prop):
            reasons[prop] = "virtual"  # kein gespeichertes Feld, nur eine Suchhilfe
            return None
        try:
            facet = (await run.call(base, prop)).facets[prop]
        except EduSharingError as exc:
            if not _refused(exc):
                raise
            await main_task  # liegt es an den Filtern, scheitert die Abfrage hier
            reasons[prop] = "refused"
            return None
        if facet.truncated or len(facet.values) > MAX_VALUES:
            reasons[prop] = "many"
            return None
        # Ein leerer Wert ist kein Wert: Er zählt weder im Kriterium noch in der Summe.
        values = [v for v in facet.values if v.value.strip()]
        if len(values) < len(facet.values):
            blank.add(prop)
        total = (await main_task).total
        if not values:
            if total:
                empty.add(prop)
            return False
        best = max(values, key=lambda v: (v.count, v.value))
        low, high = best.count, sum(v.count for v in values)
        try:
            exact, counted = await asyncio.gather(
                _criterion_exact(run, base, prop, {v.value: v.count for v in values}),
                run.call(base + (Criterion(prop, tuple(v.value for v in values)),), row_prop))
        except EduSharingError as exc:
            if not _refused(exc):
                raise
            exact, counted = False, None
        if exact and counted is not None and low <= counted.total <= min(high, total):
            return counted
        field = await vocab.field(prop)
        if high <= total and (low == high
                              or (field is not None and field.type in SINGLE_TYPES)):
            summed.add(prop)
            return high
        ranges[prop] = (low, min(high, total))
        return None

    blank: set[str] = set()
    empty: set[str] = set()
    by_field: list[Aggregate | int | bool | None] = list(await asyncio.gather(
        *[measure(f.property) for f in fill.fields]))
    main = await main_task

    labels = []
    for item in fill.fields:
        known = await vocab.field(item.property)
        labels.append(item.label or (known.caption if known and known.caption else item.property))
    name = {f.property: label for f, label in zip(fill.fields, labels, strict=True)}

    def named(reason: str) -> str:
        return ", ".join(name[p] for p in name if reasons.get(p) == reason)

    if named("many"):
        notes.add("fill_unknown",
                  "Nicht bestimmbar (zu viele verschiedene Werte oder gekürzte Werteliste): "
                  f"{named('many')}.")
    if named("virtual"):
        notes.add("fill_virtual",
                  f"Nicht bestimmbar: {named('virtual')} (Suchhilfe des Repositoriums, kein "
                  "gespeichertes Feld).")
    if named("closed"):
        notes.add("fill_closed",
                  f"Nicht bestimmbar: {named('closed')} (auf dieser Instanz kein Feld mit "
                  "Werteliste und nicht freigegeben; es wird nichts abgefragt).")
    if named("refused"):
        notes.add("fill_refused",
                  f"Nicht bestimmbar: {named('refused')} (das Repositorium zählt dieses Feld "
                  "nicht).")
    if ranges:
        parts = "; ".join(f"{name[p]}: zwischen {low} und {high}"
                          for p, (low, high) in ranges.items())
        notes.add("fill_range",
                  "Nicht genau bestimmbar, weil das Repositorium das Feld nicht als Filter "
                  "zählt und ein Material mehrere Werte tragen kann. Materialien mit Wert "
                  f"insgesamt: {parts}.")
    if summed:
        text = ("Aus der Summe der einzelnen Werte gerechnet, weil das Repositorium das "
                "Feld nicht als Filter zählt. Das stimmt, solange jedes Material höchstens "
                "einen Wert trägt (laut Metadatenset einwertig); sonst ist die Zahl zu "
                f"hoch: {', '.join(name[p] for p in name if p in summed)}.")
        if rows_spec:
            text += " Je Zeile ist das nicht möglich."
        notes.add("fill_summed", text)
    if empty:
        notes.add("fill_empty",
                  "Kein einziger Wert kommt vor: "
                  f"{', '.join(name[p] for p in name if p in empty)}.")
    if blank:
        notes.add("fill_blank",
                  "Leere Werte zählen nicht als Wert: "
                  f"{', '.join(name[p] for p in name if p in blank)}.")
    filtered = [label for label, got in zip(labels, by_field, strict=True) if got is True]
    if filtered:
        notes.add("fill_filtered",
                  f"{', '.join(filtered)}: steht in den Filtern, jedes gefilterte Material "
                  "hat dort einen Wert.")
    capped = False

    def filled_of(got: Aggregate | int | bool | None, total: int | None,
                  row: str | None, row_cut: bool) -> int | None:
        nonlocal capped
        if got is None or total is None:
            return None
        if got is True:
            return total
        if got is False:
            return 0
        if isinstance(got, int):
            return got if row is None else None
        if row is None:
            value: int | None = got.total
        else:
            facet = got.facets[row_prop]
            found = {v.value: v.count for v in facet.values}
            value = found.get(row, None if facet.truncated or row_cut else 0)
        if value is not None and value > total:
            capped, value = True, total
        return value

    def shown(filled: int | None, total: int | None) -> Number | None:
        if fill.show == "filled":
            return filled
        if fill.show == "missing":
            return None if filled is None or total is None else total - filled
        return _rate(filled, total)

    provenance_rows = None
    if rows_spec is None:
        columns = [{"id": "filled", "label": "mit Wert", "derived": False, "total": None},
                   {"id": "missing", "label": "ohne Wert", "derived": True, "total": None},
                   {"id": "rate", "label": "Füllgrad %", "derived": True, "total": None}]
        out_rows = []
        for item, label, got in zip(fill.fields, labels, by_field, strict=True):
            filled = filled_of(got, main.total, None, False)
            out_rows.append({
                "key": item.property, "label": label, "kind": "value",
                "values": [filled, None if filled is None else main.total - filled,
                           _rate(filled, main.total)]})
        notes.add("fill_base", f"Grundgesamtheit: {main.total} Materialien.")
        column_notes = [{"id": c["id"], "filters": [], "formula": None} for c in columns]
    else:
        facet = main.facets[rows_spec.property]
        counts = {v.value: v.count for v in facet.values}
        if facet.truncated:
            notes.add("facet_truncated",
                      f"Die Werteliste von '{row_prop}' wurde vom Repositorium gekürzt; "
                      "seltene Werte fehlen.")
        include = None
        if rows_spec.include:
            include = await resolve_values(vocab, rows_spec.property, rows_spec.include)
            keys = [item["value"] for item in include]
        else:
            keys = list(counts)
        restrict = next((set(c.values) for c in base if c.property == row_prop), None)
        if restrict is not None:
            keys = [k for k in keys if k in restrict]
        rows = [_Row(k, await vocab.label(rows_spec.property, k), "value") for k in keys]
        if rows_spec.match or rows_spec.vocab:
            rows = await _select_rows(rows, rows_spec, vocab, notes,
                                      _row_filter(base, row_prop))
        count_column = _Column("count", "Anzahl")
        for row in rows:
            count_column.cells[row.key] = counts.get(row.key, None if facet.truncated else 0)
        rows = _sort_rows(rows, [count_column], rows_spec, include, False)
        if rows_spec.top is not None and len(rows) > rows_spec.top:
            notes.add("rows_top",
                      f"Angezeigt werden die ersten {rows_spec.top} von {len(rows)} Zeilen.")
            _unused_after_top(notes, rows, rows[: rows_spec.top])
            rows = rows[: rows_spec.top]
        derived = fill.show != "filled"
        suffix = {"rate": " %", "missing": ": ohne Wert", "filled": ""}[fill.show]
        columns = [{"id": "count", "label": "Anzahl", "derived": False, "total": main.total}]
        for item_id, label, got in zip(ids, labels, by_field, strict=True):
            columns.append({"id": item_id, "label": label + suffix, "derived": derived,
                            "total": shown(filled_of(got, main.total, None, False),
                                           main.total)})
        out_rows = []
        for row in rows:
            total = count_column.cells[row.key]
            out_rows.append({
                "key": row.key, "label": row.label, "kind": "value",
                "values": [total, *[shown(filled_of(got, total, row.key, facet.truncated), total)
                                    for got in by_field]]})
        row_field = await vocab.field(row_prop)
        one_valued = row_prop in prefix_fields or (
            row_field is not None and row_field.type in SINGLE_TYPES)
        if len(rows) > 1 and not one_valued:
            notes.add("rows_overlap",
                      f"Ein Dokument kann mehrere Werte von '{row_prop}' tragen und steht dann "
                      "in mehreren Zeilen.")
        provenance_rows = {"property": row_prop, "mode": "facet",
                           "include": [i["value"] for i in include] if include else None,
                           "groups": []}
        column_notes = [{"id": c["id"], "filters": [], "formula": None} for c in columns]
    notes.add("fill_derived",
              "„Ohne Wert“ und Füllgrad sind berechnet: Gesamtzahl minus Materialien mit "
              "mindestens einem Wert im Feld.")
    if capped:
        notes.add("fill_capped",
                  "Das Repositorium zählte für ein Feld mehr Materialien mit Wert als "
                  "insgesamt; der Wert ist auf die Gesamtzahl begrenzt.")
    fetched = [r.fetched_at for r in run.results]
    inst = client.instance
    result: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION, "instance": spec.instance,
        "columns": columns, "rows": out_rows,
        "provenance": {
            "fetched_at": max(fetched) if fetched else None,
            "oldest_fetched_at": min(fetched) if fetched else None,
            "cached": bool(run.results) and all(r.cached for r in run.results),
            "queries": len(run.results), "notes": list(notes),
            "instance": {"id": inst.id, "base_url": inst.base_url, "repository": inst.repository,
                         "metadataset": inst.metadataset, "query": inst.query},
            "spec": spec.model_dump(mode="json", exclude={"refresh"}),
            "rows": provenance_rows, "filters": base_described, "columns": column_notes,
        },
    }
    # woran Grafiken erkennen, was sie zeigen dürfen (nie Anzahl und Anteil gemischt)
    result["fill"] = {"show": fill.show, "by_rows": rows_spec is not None}
    if rows_spec is None:
        result["total_row"] = False
        result["base_total"] = main.total
    return result


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
            offered = [v for v in facet.values
                       if not spec.columns.match or v.value.startswith(spec.columns.match)]
            top = sorted(offered, key=lambda v: (-v.count, v.value))[: spec.columns.top]
            chosen = [{"value": v.value, "label": await vocab.label(prop, v.value)}
                      for v in top]
            if len(offered) > len(top) or facet.truncated:
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


async def known_values(vocab: VocabStore, prop: str) -> set[str]:
    """Die Werte des Vokabulars. Nur Felder mit Werteliste: Bei freiem Text wäre „nicht im
    Vokabular“ eine Liste von Werten einzelner Datensätze."""
    field = await vocab.field(prop)
    if field is None or not field.values:
        raise QueryError(
            f"'{prop}' hat keine Werteliste; ein Abgleich mit dem Vokabular geht nur bei "
            "Feldern mit Werteliste.")
    return {v.id for v in field.values}


async def _rollup_rows(vocab: VocabStore, rows_spec, notes: Notes) -> list[_Row]:
    """Die oberste Ebene des Vokabularbaums als Zeilen, jede mit allen ihren Unterwerten.
    Eine einzelne Wurzel (der Name des Vokabulars) zählt nicht als Ebene."""
    prop = rows_spec.property
    field = await vocab.field(prop)
    values = [v for v in (field.values if field else ())
              if not rows_spec.match or v.id.startswith(rows_spec.match)]
    if not values:
        raise QueryError(
            f"'{prop}' hat auf dieser Instanz keine passende Werteliste; die oberste Ebene "
            "lässt sich nicht bilden.")
    ids = {v.id for v in values}
    children: dict[str | None, list] = {}
    for v in values:
        parent = v.parent if v.parent in ids and v.parent != v.id else None
        children.setdefault(parent, []).append(v)
    tops, skipped = children.get(None, []), []
    while len(tops) == 1 and children.get(tops[0].id):
        skipped.append(tops[0])
        tops = children[tops[0].id]
    rows = []
    for top in tops:
        members, stack = [], [top]
        while stack:
            node = stack.pop()
            if node.id in members:
                continue
            members.append(node.id)
            stack.extend(children.get(node.id, []))
        rows.append(_Row(top.id, top.caption or top.id, "group", tuple(members)))
    if len(rows) > MAX_GROUPS:
        raise QueryError(f"Die oberste Ebene von '{prop}' hat {len(rows)} Werte, erlaubt "
                         f"sind {MAX_GROUPS}.")
    text = ("Jede Zeile zählt die Materialien mit dem Wert selbst oder einem seiner "
            "Unterwerte im Vokabular, jedes Material je Zeile einmal.")
    if skipped:
        text += (" Die Wurzel des Vokabulars („" + skipped[0].caption
                 + "“) ist keine eigene Zeile.")
    text += (" Die Gesamtzahl einer Spalte zählt alle Materialien, auch die außerhalb "
             "dieser Zeilen.")
    notes.add("rows_rollup", text)
    return rows


def _row_filter(base, prop: str) -> bool:
    return any(c.property == prop for c in base)


def _unused_after_top(notes: Notes, before: list[_Row], shown: list[_Row]) -> None:
    """Der Hinweis auf die ergänzten Nullzeilen spricht nur von dem, was zu sehen ist."""
    total, left = sum(r.unused for r in before), sum(r.unused for r in shown)
    if left == total:
        return
    for note in notes:
        if note["code"] == "rows_unused":
            note["text"] = (
                f"{total} Werte der Werteliste trägt kein Material; "
                + (f"{left} davon stehen mit 0 in der Tabelle, die übrigen schneidet"
                   if left else "sie stehen nicht in der Tabelle, weil")
                + " die Begrenzung der Zeilen" + (" ab." if left else " sie abschneidet."))
            if not left:
                note["code"] = "rows_unused_cut"


async def _select_rows(rows: list[_Row], rows_spec, vocab: VocabStore, notes: Notes,
                       filtered: bool = False) -> list[_Row]:
    """``match`` und ``vocab``: Auswahl unter den vorkommenden Werten."""
    total = len(rows)
    unused: list = []
    if rows_spec.match:
        rows = [r for r in rows if r.key.startswith(rows_spec.match)]
    if rows_spec.vocab:
        known = await known_values(vocab, rows_spec.property)
        inside = rows_spec.vocab == "inside"
        rows = [r for r in rows if (r.key in known) == inside]
        if inside and not filtered:  # ein Filter auf dem Zeilenfeld gilt auch hier
            # auch Werte der Werteliste, die kein Material trägt: die größten Lücken
            have = {r.key for r in rows}
            field = await vocab.field(rows_spec.property)
            unused = [v for v in field.values if v.id not in have
                      and (not rows_spec.match or v.id.startswith(rows_spec.match))]
            rows += [_Row(v.id, v.caption or v.id, "value", unused=True) for v in unused]
    what = []
    if rows_spec.match:
        what.append(f"die mit „{rows_spec.match}“ beginnen")
    if rows_spec.vocab:
        what.append("die das Vokabular des Feldes "
                    + ("kennt" if rows_spec.vocab == "inside" else "nicht kennt"))
    if unused:
        notes.add("rows_unused",
                  f"{len(unused)} Werte der Werteliste trägt kein Material; sie stehen mit 0 "
                  "in der Tabelle.")
    notes.add("rows_selected",
              f"Gezeigt sind nur Werte, {' und '.join(what)}: {len(rows) - len(unused)} von "
              f"{total} vorkommenden Werten. Die Gesamtzahl einer Spalte zählt alle "
              "Materialien, auch die mit anderen Werten oder ohne Wert.")
    return rows


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
        for value in facet_counts.get("", {}):  # Zeilen ohne Treffer in einer Spalte
            seen.setdefault(value)
        keys = list(seen)
    restrict = next((set(c.values) for c in base if c.property == prop), None)
    if restrict is not None:
        keys = [k for k in keys if k in restrict]
    return [_Row(k, await vocab.label(prop, k), "value") for k in keys]


def _sort_rows(rows: list[_Row], columns: list[_Column], rows_spec, include, grouped: bool,
               across: bool = False, row_totals: dict[str, int] | None = None) -> list[_Row]:
    if not rows_spec or not rows:
        return rows
    sort = rows_spec.sort or ("given" if (grouped and not rows_spec.rollup)
                              or include is not None else "count")
    if sort == "given":
        return rows
    if sort == "label":
        return sorted(rows, key=lambda r: (r.label.casefold(), r.key))
    first = columns[0]
    counted = [c for c in columns if not c.derived]

    def by_count(row: _Row):
        if across and row_totals and row.key in row_totals:
            value = row_totals[row.key]  # Spalten aus einem Feld: es zählt die Zeile insgesamt
        elif across:
            cells = [c.cells.get(row.key) for c in counted]
            value = None if all(v is None for v in cells) else sum(v or 0 for v in cells)
        else:
            value = first.cells.get(row.key)
        return (value is None, -(value or 0), row.label.casefold(), row.key)

    return sorted(rows, key=by_count)


def _begins(value: str, other: str) -> bool:
    return other.casefold().startswith(value.casefold())


async def _criterion_exact(run: _Run, criteria: tuple[Criterion, ...], prop: str,
                           counts: dict[str, int]) -> bool:
    """Probe: Zählt das Kriterium einen Wert so wie die Facette? Ein Kriterium, das anders
    vergleicht (Textsuche, Teiltreffer), fällt hier auf.

    Live gemessen vergleichen manche Felder ohne Groß/Klein und als Wortanfang („de“ trifft
    auch „DE“, „de_DE“, „Deutsch“). Für „irgendein Wert“ ist das unschädlich. Die Probe nimmt
    deshalb den häufigsten Wert, mit dem kein anderer beginnt, und verlangt dort Gleichheit;
    gibt es keinen, darf der häufigste um die Werte höher liegen, die mit ihm beginnen."""
    values = {v: n for v, n in counts.items() if v.strip() and n}
    if not values:
        return True
    ranked = sorted(values, key=lambda v: (-values[v], v))
    alone = next((v for v in ranked
                  if not any(o != v and _begins(v, o) for o in values)), None)
    pick = alone if alone is not None else ranked[0]
    slack = 0 if alone is not None else sum(
        n for o, n in values.items() if o != pick and _begins(pick, o))
    try:
        probe = (await run.call(criteria + (Criterion(prop, (pick,)),))).total
    except EduSharingError as exc:
        if not _refused(exc):
            raise
        return False
    return values[pick] <= probe <= values[pick] + slack


async def _count_with_any_value(run: _Run, criteria: tuple[Criterion, ...], prop: str,
                                counts: dict[str, int], truncated: bool,
                                total: int | None = None) -> int | None:
    """Zahl der Dokumente mit mindestens einem Wert im Feld; ``None``, wenn nicht bestimmbar.
    Ein leerer Wert ist kein Wert. Die Zählung gilt nur, wenn sie zwischen häufigstem Wert
    und Summe liegt; ob das Kriterium genau vergleicht, prüft der Aufrufer."""
    counts = {v: n for v, n in counts.items() if v.strip()}
    values = tuple(counts)
    if truncated or len(values) > MAX_VALUES:
        return None
    if not values:
        return 0
    try:
        counted = await run.call(criteria + (Criterion(prop, values),))
    except EduSharingError as exc:
        if not _refused(exc):
            raise
        return None  # das Repositorium zählt dieses Feld nicht als Filter
    high = sum(counts.values()) if total is None else min(sum(counts.values()), total)
    if not max(counts.values()) <= counted.total <= high:
        return None
    return counted.total


def _share(value: Number | None, total: Number | None) -> float | None:
    if value is None or not total:
        return None
    try:
        return round(value / total * 100, 2)
    except (OverflowError, ValueError):
        return None
