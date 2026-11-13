"""Filter in der Form der Schnittstelle und ihre Auflösung zu Kriterien."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictStr

from .edusharing.client import MAX_VALUE_LENGTH, Criterion, Facet
from .edusharing.errors import EduSharingError
from .edusharing.vocab import VocabStore
from .errors import Notes, QueryError

Name = Annotated[StrictStr, Field(min_length=1, max_length=200, pattern=r"^\S+$")]
Value = Annotated[StrictStr, Field(max_length=MAX_VALUE_LENGTH)]


class LiteralValue(BaseModel):
    """Wert, der unverändert als Filter gilt, ohne Abgleich mit dem Vokabular."""

    model_config = ConfigDict(extra="forbid")
    value: Value
    literal: Literal[True]


ValueIn = Value | LiteralValue


class FilterIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    property: Name
    values: list[ValueIn] = Field(min_length=1, max_length=2000)


async def resolve_values(
    vocab: VocabStore, property: str, values: list[ValueIn]
) -> list[dict[str, str]]:
    """Löst Labels und URIs auf; doppelte Treffer fallen zusammen, die Reihenfolge bleibt."""
    resolved: dict[str, dict[str, str]] = {}
    for item in values:
        if isinstance(item, LiteralValue):
            res = await vocab.resolve(property, item.value, literal=True)
        else:
            res = await vocab.resolve(property, item)
        resolved.setdefault(res.value, {"value": res.value, "label": res.label, "how": res.how})
    return list(resolved.values())


async def resolve_filters(
    vocab: VocabStore, filters: list[FilterIn]
) -> tuple[list[Criterion], list[dict[str, Any]]]:
    """Liefert die Kriterien und, für die Herkunftsangabe, wie jeder Wert aufgelöst wurde."""
    seen: set[str] = set()
    criteria: list[Criterion] = []
    described: list[dict[str, Any]] = []
    for flt in filters:
        if flt.property in seen:
            raise EduSharingError(
                "invalid_query",
                f"Das Feld '{flt.property}' kommt in mehreren Filtern vor. "
                "Mehrere Werte gehören in einen Filter.")
        seen.add(flt.property)
        values = await resolve_values(vocab, flt.property, flt.values)
        criteria.append(Criterion(flt.property, tuple(v["value"] for v in values)))
        described.append({"property": flt.property, "values": values})
    return criteria, described


def check_prefix_shape(criteria, prefix_fields: set[str]) -> None:
    for crit in criteria:
        if crit.property in prefix_fields and len(crit.values) > 1:
            raise QueryError(
                f"'{crit.property}' nimmt als Filter nur einen Wert. Für mehrere Lizenzen "
                "das Feld als Zeilen mit Gruppen verwenden oder über 'license' filtern.")


async def check_prefix_values(
    occurring: Callable[[str], Awaitable[Facet]], criteria, prefix_fields: set[str],
    notes: Notes, column_prop: str | None = None,
) -> None:
    """Ein Kriterium auf einem Präfix-Feld zählt auch alle längeren Schlüssel mit. Erlaubt
    sind deshalb nur Werte, die kein Anfang eines anderen vorkommenden Schlüssels sind."""
    wanted: dict[str, set[str]] = {}
    for crit in criteria:
        if crit.property in prefix_fields:
            wanted.setdefault(crit.property, set()).update(crit.values)
    for prop, values in wanted.items():
        if "" in values:
            notes.add("prefix_empty", 
                f"Der leere Wert für '{prop}' zählt alle Dokumente, bei denen das Feld "
                "gesetzt ist.")
            values = values - {""}
        if not values:
            continue
        facet = await occurring(prop)
        if facet.truncated:
            raise QueryError(
                f"Die Werte von '{prop}' lassen sich nicht vollständig prüfen; das Feld "
                "kann deshalb nicht als Filter dienen.")
        keys = [v.value for v in facet.values]
        for value in sorted(values):
            longer = sorted(k for k in keys if k != value and k.startswith(value))
            if longer and prop == column_prop:
                raise QueryError(
                    f"'{prop}' taugt nicht als Spaltenfeld: Die Spalte '{value}' würde auch "
                    f"{', '.join(longer[:6])} mitzählen (das Repositorium vergleicht hier nur "
                    f"den Anfang). Stattdessen '{prop}' als Zeilen verwenden.")
            if longer:
                raise QueryError(
                    f"Der Filter '{prop}' = '{value}' würde auch {', '.join(longer[:6])} "
                    f"mitzählen (das Repositorium vergleicht hier nur den Anfang). "
                    f"Stattdessen '{prop}' als Zeilen verwenden oder über 'license' filtern.")
