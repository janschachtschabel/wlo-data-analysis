"""Beschreibung einer Abfrage. Einziges Format, in dem Zahlen angefordert werden."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictStr, model_validator

from ..filters import FilterIn, Name, ValueIn
from .formula import FUNCTIONS

MAX_SERIES = 40
MAX_GROUPS = 300
MAX_CALLS = 400

Ident = Annotated[StrictStr, Field(pattern=r"^[a-z_][a-z0-9_]{0,39}$")]
Label = Annotated[StrictStr, Field(min_length=1, max_length=200)]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Group(_Model):
    id: Ident | None = None
    label: Label
    values: list[ValueIn] = Field(min_length=1, max_length=2000)


Match = Annotated[StrictStr, Field(min_length=1, max_length=300)]


class Rows(_Model):
    property: Name
    include: list[ValueIn] | None = Field(default=None, min_length=1, max_length=2000)
    groups: list[Group] | None = Field(default=None, min_length=1, max_length=MAX_GROUPS)
    group_mode: Literal["auto", "sum", "distinct"] = "auto"
    sort: Literal["count", "label", "given"] | None = None
    top: int | None = Field(default=None, ge=1, le=5000)
    other: bool = False
    missing: bool = False
    # nur Werte, die so beginnen (z. B. die URI eines von mehreren Vokabularen im Feld)
    match: Match | None = None
    # nur Werte, die das Vokabular des Feldes nicht kennt (outside) bzw. kennt (inside)
    vocab: Literal["outside", "inside"] | None = None

    @model_validator(mode="after")
    def _consistent(self) -> Rows:
        if self.groups and self.include:
            raise ValueError("include und groups schließen sich aus")
        if (self.match or self.vocab) and (self.groups or self.include or self.other
                                           or self.missing):
            raise ValueError("match und vocab wählen unter den vorkommenden Werten aus und "
                             "gehen nicht zusammen mit include, groups, other oder missing")
        if self.other and self.top is None:
            raise ValueError("other braucht top")
        return self


class Series(_Model):
    id: Ident
    label: Label
    filters: list[FilterIn] = Field(default_factory=list, max_length=10)
    formula: Annotated[StrictStr, Field(min_length=1, max_length=500)] | None = None

    @model_validator(mode="after")
    def _either(self) -> Series:
        if self.formula is not None and self.filters:
            raise ValueError("eine Spalte hat entweder Filter oder eine Formel")
        return self


class Columns(_Model):
    property: Name
    include: list[ValueIn] | None = Field(default=None, min_length=1, max_length=MAX_SERIES)
    top: int = Field(default=12, ge=1, le=MAX_SERIES)
    match: Match | None = None

    @model_validator(mode="after")
    def _consistent(self) -> Columns:
        if self.match and self.include:
            raise ValueError("match geht nicht zusammen mit include")
        return self



MAX_FILL_FIELDS = 30


class FillField(_Model):
    property: Name
    label: Label | None = None


class Fill(_Model):
    """Füllgrade: je Feld die Zahl der Dokumente mit mindestens einem Wert. Ohne ``rows``
    eine Zeile je Feld; mit ``rows`` je Feld eine Spalte, ``show`` sagt, was in ihr steht."""

    fields: list[FillField] = Field(min_length=1, max_length=MAX_FILL_FIELDS)
    show: Literal["rate", "missing", "filled"] = "rate"

    @model_validator(mode="after")
    def _unique(self) -> Fill:
        props = [f.property for f in self.fields]
        if len(props) != len(set(props)):
            raise ValueError("jedes Feld nur einmal")
        return self


class VocabCheck(_Model):
    """Je Feld: wie viele der vorkommenden Werte das Vokabular nicht kennt."""

    fields: list[FillField] = Field(min_length=1, max_length=MAX_FILL_FIELDS)

    @model_validator(mode="after")
    def _unique(self) -> VocabCheck:
        props = [f.property for f in self.fields]
        if len(props) != len(set(props)):
            raise ValueError("jedes Feld nur einmal")
        return self


class QuerySpec(_Model):
    instance: Annotated[StrictStr, Field(min_length=1, max_length=64)]
    content_type: StrictStr = "FILES"
    filters: list[FilterIn] = Field(default_factory=list, max_length=20)
    rows: Rows | None = None
    series: list[Series] = Field(default_factory=list, max_length=MAX_SERIES)
    columns: Columns | None = None
    fill: Fill | None = None
    vocab_check: VocabCheck | None = None
    percent: Literal["none", "column", "row"] = "none"
    refresh: bool = False

    @model_validator(mode="after")
    def _consistent(self) -> QuerySpec:
        if self.series and self.columns:
            raise ValueError("series und columns schließen sich aus")
        if self.fill and (self.series or self.columns or self.percent != "none"):
            raise ValueError("fill (Füllgrade) geht nicht zusammen mit series, columns "
                             "oder percent")
        if self.vocab_check and (self.series or self.columns or self.fill or self.rows
                                 or self.percent != "none"):
            raise ValueError("vocab_check geht nicht zusammen mit rows, series, columns, "
                             "fill oder percent")
        ids = [s.id for s in self.series]
        if len(ids) != len(set(ids)):
            raise ValueError("Spalten-IDs müssen eindeutig sein")
        if set(ids) & set(FUNCTIONS):
            raise ValueError("round, max und min sind als Spalten-ID nicht erlaubt")
        return self
