"""Fehler, die an der Abfrage des Aufrufers liegen."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any


class QueryError(Exception):
    """Die Abfrage-Beschreibung ist fehlerhaft; die Meldung ist für die Oberfläche bestimmt."""


class Notes(list):
    """Hinweise zu einem Ergebnis: je Eintrag ein fester ``code`` (für Vergleiche zwischen
    Läufen und für die Oberfläche) und ein deutscher Satz."""

    def add(self, code: str, text: str) -> None:
        self.append({"code": code, "text": text})


_MESSAGES = {
    "missing": "fehlt", "extra_forbidden": "unbekanntes Feld",
    "string_pattern_mismatch": "enthält unerlaubte Zeichen oder hat die falsche Länge",
    "string_too_long": "ist zu lang", "string_too_short": "ist zu kurz",
    "too_long": "hat zu viele Einträge", "too_short": "hat zu wenige Einträge",
    "literal_error": "ist kein erlaubter Wert", "union_tag_invalid": "ist kein erlaubter Wert",
    "union_tag_not_found": "fehlt",
}


def describe_validation(errors: Sequence[Any]) -> str:
    """Kurze deutsche Meldung mit dem Ort des ersten Fehlers, ohne die Eingabe zu wiederholen."""
    err = errors[0]
    where = ".".join(str(p) for p in err["loc"]) or "Eingabe"
    if err["type"] == "value_error":
        what = str(err["msg"]).removeprefix("Value error, ")
    else:
        what = _MESSAGES.get(err["type"], "hat keinen gültigen Wert")
    more = f" (und {len(errors) - 1} weitere Fehler)" if len(errors) > 1 else ""
    return f"Ungültig: '{where}' – {what}{more}."


def error_body(kind: str, detail: str, **extra: Any) -> dict[str, Any]:
    """Die eine Form, in der die API Fehler meldet: ein deutscher Satz und eine feste Art."""
    return {"detail": detail, "kind": kind, **extra}


class UnknownInstance(Exception):
    """Die genannte Instanz ist nicht eingerichtet."""

    def __init__(self, instance_id: str) -> None:
        super().__init__(f"Die Instanz '{instance_id[:64]}' gibt es nicht.")
