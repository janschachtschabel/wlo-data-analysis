"""Spezifikationen der vier Arten von Definitionen und ihre Prüfung."""

from __future__ import annotations

import json
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictStr, ValidationError, model_validator

from ..engine.formula import compile_formula
from ..engine.spec import Label, QuerySpec
from ..errors import QueryError
from ..filters import FilterIn

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


class TemplateSpec(_Model):
    instance: InstanceId | None = None
    filter_ids: list[DefId] = Field(default_factory=list, max_length=10)
    title: Label
    sections: list[Annotated[TextSection | DataSection, Field(discriminator="type")]] = Field(
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


_MESSAGES = {
    "missing": "fehlt", "extra_forbidden": "unbekanntes Feld",
    "string_pattern_mismatch": "enthält unerlaubte Zeichen oder hat die falsche Länge",
    "string_too_long": "ist zu lang", "string_too_short": "ist zu kurz",
    "too_long": "hat zu viele Einträge", "too_short": "hat zu wenige Einträge",
    "literal_error": "ist kein erlaubter Wert", "union_tag_invalid": "ist kein erlaubter Wert",
    "union_tag_not_found": "fehlt",
}


def describe(exc: ValidationError) -> str:
    """Kurze deutsche Meldung mit dem Ort des ersten Fehlers, ohne die Eingabe zu wiederholen."""
    err = exc.errors()[0]
    where = ".".join(str(p) for p in err["loc"]) or "spec"
    if err["type"] == "value_error":
        what = str(err["msg"]).removeprefix("Value error, ")
    else:
        what = _MESSAGES.get(err["type"], "hat keinen gültigen Wert")
    more = f" (und {len(exc.errors()) - 1} weitere Fehler)" if len(exc.errors()) > 1 else ""
    return f"Ungültig: '{where}' – {what}{more}."


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
        raise InvalidDefinition(describe(exc)) from None
    for query in queries_of(spec):
        query.refresh = False
        for series in query.series:
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
