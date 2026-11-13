"""Fehler, die an der Abfrage des Aufrufers liegen."""

from __future__ import annotations


class QueryError(Exception):
    """Die Abfrage-Beschreibung ist fehlerhaft; die Meldung ist für die Oberfläche bestimmt."""


class Notes(list):
    """Hinweise zu einem Ergebnis: je Eintrag ein fester ``code`` (für Vergleiche zwischen
    Läufen und für die Oberfläche) und ein deutscher Satz."""

    def add(self, code: str, text: str) -> None:
        self.append({"code": code, "text": text})
