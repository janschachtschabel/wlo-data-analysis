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
    # oberste Ebene des Vokabularbaums; jede Zeile zählt den Wert samt allen Unterwerten
    rollup: Literal["top"] | None = None

    @model_validator(mode="after")
    def _consistent(self) -> Rows:
        if self.groups and self.include:
            raise ValueError("include und groups schließen sich aus")
        if self.rollup and (self.groups or self.include or self.vocab or self.other):
            raise ValueError("rollup bildet die Zeilen aus dem Vokabular und geht nicht "
                             "zusammen mit include, groups, vocab oder other")
        if (self.match or self.vocab) and not self.rollup and (
                self.groups or self.include or self.other or self.missing):
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


MAX_PROFILE_ROWS = 60


class Profile(_Model):
    """Ausprägungen eines Merkmals je Zeile, etwa Kosten je Quelle: je Zeilenwert eine
    Abfrage mit der Facette des Merkmals. Geht deshalb auch bei Feldern, die das
    Repositorium nicht als Filter zählt."""

    property: Name
    top: int = Field(default=8, ge=1, le=MAX_SERIES)
    show: Literal["share", "count"] = "share"
    missing: bool = True  # Spalte „ohne Angabe“ (nur bei einwertigen Feldern bestimmbar)


class Sizes(_Model):
    """Größenklassen: wie viele Werte eines Feldes (etwa Bezugsquellen) tragen 1, 2 bis 4,
    5 bis 19 … Materialien, und wie viele Materialien liegen in jeder Klasse."""

    property: Name
    bounds: list[Annotated[int, Field(ge=1, le=10_000_000)]] = Field(
        default=[1, 2, 5, 20, 100, 1000], min_length=1, max_length=12)

    @model_validator(mode="after")
    def _ascending(self) -> Sizes:
        if self.bounds[0] != 1 or any(a >= b for a, b in zip(self.bounds, self.bounds[1:],
                                                             strict=False)):
            raise ValueError("bounds beginnt bei 1 und steigt streng an")
        return self


class Gaps(_Model):
    """Lückenliste: macht aus einer Matrix (rows × columns) eine Liste der Felder, die am
    weitesten hinter dem zurückbleiben, was Zeile und Spalte erwarten lassen."""

    limit: int = Field(default=30, ge=1, le=200)
    min_expected: float = Field(default=20, ge=1, le=1_000_000)
    below: float = Field(default=50, gt=0, le=100)  # nur Felder unter so viel % der Erwartung


SNAPSHOT_ANALYSES = ("freitext", "beschreibung", "titel", "schlagwoerter", "duenn",
                     "vollstaendigkeit", "dubletten", "alter", "adressen", "jahrgang",
                     "rechner")


class SnapshotQuery(_Model):
    """Auswertung über den lokalen Abzug (Merkmale je Material, keine Inhalte): Füllgrade
    der Freitextfelder, Längen, Dubletten, Alter. Mit ``by`` je Quelle oder Bezugsquelle."""

    analysis: Literal[SNAPSHOT_ANALYSES]  # type: ignore[valid-type]
    by: Literal["source", "publisher"] | None = None
    top: int = Field(default=25, ge=1, le=200)
    show: Literal["share", "count"] = "share"

    @model_validator(mode="after")
    def _shape(self) -> SnapshotQuery:
        if self.by and self.analysis in ("jahrgang", "rechner"):
            raise ValueError(f"'{self.analysis}' gibt es nur für den ganzen Bestand (ohne by)")
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
    profile: Profile | None = None
    sizes: Sizes | None = None
    snapshot: SnapshotQuery | None = None
    gaps: Gaps | None = None
    percent: Literal["none", "column", "row"] = "none"
    refresh: bool = False

    @model_validator(mode="after")
    def _consistent(self) -> QuerySpec:
        if self.series and self.columns:
            raise ValueError("series und columns schließen sich aus")
        if self.fill and (self.series or self.columns or self.percent != "none"):
            raise ValueError("fill (Füllgrade) geht nicht zusammen mit series, columns "
                             "oder percent")
        if self.fill and self.rows and self.rows.rollup:
            raise ValueError("fill geht nicht zusammen mit rows.rollup")
        if self.vocab_check and (self.series or self.columns or self.fill or self.rows
                                 or self.percent != "none"):
            raise ValueError("vocab_check geht nicht zusammen mit rows, series, columns, "
                             "fill oder percent")
        other = (self.series or self.columns or self.fill or self.vocab_check
                 or self.percent != "none")
        if self.snapshot and (other or self.rows or self.profile or self.sizes
                              or self.filters):
            raise ValueError("snapshot (lokaler Abzug) steht für sich: ohne filters, rows, "
                             "series, columns, fill, vocab_check, profile, sizes, percent")
        if self.gaps and (self.rows is None or self.columns is None or self.fill
                          or self.vocab_check or self.profile or self.sizes
                          or self.percent != "none"):
            raise ValueError("gaps (Lückenliste) braucht rows und columns und geht nicht "
                             "zusammen mit fill, vocab_check, profile, sizes oder percent")
        if self.sizes and (other or self.rows or self.profile):
            raise ValueError("sizes geht nicht zusammen mit rows, series, columns, fill, "
                             "vocab_check, profile oder percent")
        if self.profile:
            rows = self.rows
            if other:
                raise ValueError("profile geht nicht zusammen mit series, columns, fill, "
                                 "vocab_check oder percent")
            if rows is None or (rows.top is None and not rows.include):
                raise ValueError("profile braucht rows mit top oder include (je Zeile eine "
                                 "Abfrage)")
            if rows.groups or rows.other or rows.missing or rows.rollup or rows.vocab:
                raise ValueError("profile geht nicht zusammen mit Gruppen, „Übrige“, „ohne "
                                 "Wert“, rollup oder vocab der Zeilen")
            if (rows.top or 0) > MAX_PROFILE_ROWS or len(rows.include or []) > MAX_PROFILE_ROWS:
                raise ValueError(f"profile: höchstens {MAX_PROFILE_ROWS} Zeilen")
        ids = [s.id for s in self.series]
        if len(ids) != len(set(ids)):
            raise ValueError("Spalten-IDs müssen eindeutig sein")
        if set(ids) & set(FUNCTIONS):
            raise ValueError("round, max und min sind als Spalten-ID nicht erlaubt")
        return self
