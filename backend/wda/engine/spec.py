"""Beschreibung einer Abfrage. Einziges Format, in dem Zahlen angefordert werden."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictStr, model_validator

from ..filters import FilterIn, Name, ValueIn

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


class Rows(_Model):
    property: Name
    include: list[ValueIn] | None = Field(default=None, min_length=1, max_length=2000)
    groups: list[Group] | None = Field(default=None, min_length=1, max_length=MAX_GROUPS)
    group_mode: Literal["auto", "sum", "distinct"] = "auto"
    sort: Literal["count", "label", "given"] | None = None
    top: int | None = Field(default=None, ge=1, le=5000)
    other: bool = False
    missing: bool = False

    @model_validator(mode="after")
    def _consistent(self) -> Rows:
        if self.groups and self.include:
            raise ValueError("include und groups schließen sich aus")
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


class QuerySpec(_Model):
    instance: Annotated[StrictStr, Field(min_length=1, max_length=64)]
    content_type: StrictStr = "FILES"
    filters: list[FilterIn] = Field(default_factory=list, max_length=20)
    rows: Rows | None = None
    series: list[Series] = Field(default_factory=list, max_length=MAX_SERIES)
    columns: Columns | None = None
    percent: Literal["none", "column", "row"] = "none"
    refresh: bool = False

    @model_validator(mode="after")
    def _consistent(self) -> QuerySpec:
        if self.series and self.columns:
            raise ValueError("series und columns schließen sich aus")
        ids = [s.id for s in self.series]
        if len(ids) != len(set(ids)):
            raise ValueError("Spalten-IDs müssen eindeutig sein")
        return self
