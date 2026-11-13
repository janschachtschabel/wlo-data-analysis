"""Fehler, die an der Abfrage des Aufrufers liegen."""

from __future__ import annotations


class QueryError(Exception):
    """Die Abfrage-Beschreibung ist fehlerhaft; die Meldung ist für die Oberfläche bestimmt."""
