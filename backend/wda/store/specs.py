"""Spezifikationen der vier Arten von Definitionen und ihre Prüfung."""

from __future__ import annotations

import json
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictStr, ValidationError, model_validator

from ..engine.formula import compile_formula
from ..engine.spec import MAX_SERIES, Ident, Label, QuerySpec, Series
from ..errors import QueryError, describe_validation
from ..filters import FilterIn, Name, ValueIn

KINDS = ("filter", "metric", "dashboard", "template")
KIND_LABELS = {"filter": "Filter", "metric": "Kennzahl", "dashboard": "Dashboard",
               "template": "Template"}
MAX_SPEC_BYTES = 256 * 1024

DefId = Annotated[StrictStr, Field(pattern=r"^[A-Za-z0-9_-]{6,40}$")]
ItemId = Annotated[StrictStr, Field(pattern=r"^[A-Za-z0-9_-]{1,40}$")]
InstanceId = Annotated[StrictStr, Field(min_length=1, max_length=64)]
Scalar = StrictStr | int | float | bool | None


class InvalidDefinition(Exception):
    """Die Definition ist fehlerhaft; die Meldung ist für die Oberfläche bestimmt."""


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class View(_Model):
    type: Literal["table", "number", "bar", "stacked_bar", "line", "pie"] = "table"
    options: dict[Annotated[StrictStr, Field(max_length=60)], Scalar] = Field(
        default_factory=dict, max_length=40)


class FilterSpec(_Model):
    instance: InstanceId | None = None
    filters: list[FilterIn] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def _unique(self) -> FilterSpec:
        props = [f.property for f in self.filters]
        if len(props) != len(set(props)):
            raise ValueError("jedes Feld darf in einem Filter nur einmal vorkommen")
        return self


class MetricSpec(_Model):
    query: QuerySpec
    filter_ids: list[DefId] = Field(default_factory=list, max_length=10)
    view: View = Field(default_factory=View)


class _Sourced(_Model):
    """Kachel oder Abschnitt mit Zahlen: verweist auf eine Kennzahl oder trägt eine Abfrage."""

    metric_id: DefId | None = None
    query: QuerySpec | None = None
    view: View | None = None

    @model_validator(mode="after")
    def _one_source(self) -> _Sourced:
        if (self.metric_id is None) == (self.query is None):
            raise ValueError("genau eines von metric_id und query angeben")
        return self


class Layout(_Model):
    x: int = Field(ge=0, le=47)
    y: int = Field(ge=0, le=10000)
    w: int = Field(ge=1, le=48)
    h: int = Field(ge=1, le=200)


class Tile(_Sourced):
    id: ItemId
    title: Label
    layout: Layout


class DashboardSpec(_Model):
    instance: InstanceId | None = None
    filter_ids: list[DefId] = Field(default_factory=list, max_length=10)
    tiles: list[Tile] = Field(default_factory=list, max_length=40)

    @model_validator(mode="after")
    def _unique(self) -> DashboardSpec:
        _unique_ids([t.id for t in self.tiles], "Kacheln")
        return self


class TextSection(_Model):
    id: ItemId
    type: Literal["text"]
    markdown: Annotated[StrictStr, Field(max_length=20000)]


class DataSection(_Sourced):
    id: ItemId
    type: Literal["table", "chart"]
    title: Label


Cell = int | float | None


class RowSection(_Model):
    """Zeilengruppe einer Matrix. ``values`` erscheinen als eigene Zeilen, ``extra`` zählt nur
    in der Zwischensumme mit."""

    id: Ident
    label: Label
    values: list[ValueIn] = Field(default_factory=list, max_length=300)
    extra: list[ValueIn] = Field(default_factory=list, max_length=1700)

    @model_validator(mode="after")
    def _not_empty(self) -> RowSection:
        if not self.values and not self.extra:
            raise ValueError("eine Zeilengruppe braucht mindestens einen Wert")
        return self


class MatrixRows(_Model):
    property: Name
    sections: list[RowSection] = Field(min_length=1, max_length=40)
    missing: bool = True
    subtotal: Literal["distinct", "none"] = "distinct"

    @model_validator(mode="after")
    def _unique(self) -> MatrixRows:
        _unique_ids([s.id for s in self.sections], "Zeilengruppen")
        return self


class QueryBlock(_Model):
    id: Ident
    label: Label
    source: Literal["query"]
    filters: list[FilterIn] = Field(default_factory=list, max_length=20)


class ManualBlock(_Model):
    """Von Hand eingetragene Zahlen, z. B. für eine Quelle ohne Schnittstelle."""

    id: Ident
    label: Label
    source: Literal["manual"]
    as_of: Annotated[StrictStr, Field(max_length=40)] | None = None
    note: Annotated[StrictStr, Field(max_length=500)] | None = None
    totals: dict[Ident, Cell] = Field(default_factory=dict, max_length=MAX_SERIES)
    sections: dict[Ident, dict[Ident, Cell]] = Field(default_factory=dict, max_length=40)
    cells: dict[Annotated[StrictStr, Field(max_length=2000)], dict[Ident, Cell]] = Field(
        default_factory=dict, max_length=2000)


class MatrixSection(_Model):
    """Tabelle mit gegliederten Zeilen und einem Spaltenblock je Quelle."""

    id: Ident
    type: Literal["matrix"]
    title: Label
    instance: InstanceId | None = None
    rows: MatrixRows
    series: list[Series] = Field(min_length=1, max_length=MAX_SERIES)
    blocks: list[Annotated[QueryBlock | ManualBlock, Field(discriminator="source")]] = Field(
        min_length=1, max_length=12)

    @model_validator(mode="after")
    def _unique(self) -> MatrixSection:
        _unique_ids([b.id for b in self.blocks], "Blöcke")
        _unique_ids([s.id for s in self.series], "Spalten")
        return self


class TemplateSpec(_Model):
    instance: InstanceId | None = None
    filter_ids: list[DefId] = Field(default_factory=list, max_length=10)
    title: Label
    sections: list[Annotated[TextSection | DataSection | MatrixSection,
                             Field(discriminator="type")]] = Field(
        default_factory=list, max_length=60)

    @model_validator(mode="after")
    def _unique(self) -> TemplateSpec:
        _unique_ids([s.id for s in self.sections], "Abschnitte")
        return self


def _unique_ids(ids: list[str], what: str) -> None:
    if len(ids) != len(set(ids)):
        raise ValueError(f"die IDs der {what} müssen eindeutig sein")


_MODELS: dict[str, type[_Model]] = {
    "filter": FilterSpec, "metric": MetricSpec, "dashboard": DashboardSpec,
    "template": TemplateSpec}
Spec = FilterSpec | MetricSpec | DashboardSpec | TemplateSpec


def queries_of(spec: Spec) -> list[QuerySpec]:
    if isinstance(spec, MetricSpec):
        return [spec.query]
    if isinstance(spec, DashboardSpec):
        return [t.query for t in spec.tiles if t.query]
    if isinstance(spec, TemplateSpec):
        return [s.query for s in spec.sections if isinstance(s, DataSection) and s.query]
    return []


def parse_spec(kind: str, data: Any) -> Spec:
    """Prüft eine Spezifikation vollständig. Formeln werden kompiliert; ``refresh`` wird nicht
    gespeichert (neu laden ist eine Eigenschaft des Laufs)."""
    model = _MODELS.get(kind)
    if model is None:
        raise InvalidDefinition(f"Unbekannte Art '{kind}'. Erlaubt: {', '.join(KINDS)}.")
    try:
        spec = model.model_validate(data)
    except ValidationError as exc:
        raise InvalidDefinition(describe_validation(exc.errors())) from None
    all_series = []
    for query in queries_of(spec):
        query.refresh = False
        all_series += query.series
    if isinstance(spec, TemplateSpec):
        for section in spec.sections:
            if isinstance(section, MatrixSection):
                all_series += section.series
    for series in all_series:
        if series.formula is not None:
            try:
                compile_formula(series.formula)
            except QueryError as exc:
                raise InvalidDefinition(f"Spalte '{series.label}': {exc}") from None
    if len(dump_spec(spec).encode()) > MAX_SPEC_BYTES:
        raise InvalidDefinition("Die Definition ist zu groß (mehr als 256 KB).")
    return spec  # type: ignore[return-value]


def dump_spec(spec: Spec) -> str:
    return json.dumps(spec.model_dump(mode="json"), ensure_ascii=False, sort_keys=True)


def references(spec: Spec) -> dict[str, str]:
    """Verwiesene Definitionen: ID → erwartete Art."""
    found: dict[str, str] = {}
    for fid in getattr(spec, "filter_ids", []):
        found[fid] = "filter"
    items = (spec.tiles if isinstance(spec, DashboardSpec)
             else spec.sections if isinstance(spec, TemplateSpec) else [])
    for item in items:
        metric_id = getattr(item, "metric_id", None)
        if metric_id:
            if found.get(metric_id) == "filter":
                raise InvalidDefinition(
                    f"'{metric_id}' wird zugleich als Filter und als Kennzahl verwendet.")
            found[metric_id] = "metric"
    return found
