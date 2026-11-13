"""Filter in der Form der Schnittstelle und ihre Auflösung zu Kriterien."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictStr

from .edusharing.client import MAX_VALUE_LENGTH, Criterion
from .edusharing.errors import EduSharingError
from .edusharing.vocab import VocabStore

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
