from __future__ import annotations

KINDS = frozenset({
    "invalid_query",      # von uns abgelehnt, nie gesendet
    "unknown_property",   # Repositorium kennt das Filterfeld nicht
    "unknown_facet",      # Repositorium kennt das Facettenfeld nicht
    "single_value",       # mehrere Werte für ein einwertiges Feld
    "rejected",           # sonstige Ablehnung (4xx), Grund vom Repositorium verborgen
    "unavailable",        # 5xx oder keine Verbindung
    "timeout",
    "bad_response",       # Antwort nicht im erwarteten Format
})


class EduSharingError(Exception):
    """Fehler beim Abruf. ``message`` ist für die Oberfläche bestimmt und enthält nie
    Stacktraces, Zugangsdaten oder ungeprüften Text des Repositoriums."""

    def __init__(self, kind: str, message: str, status: int | None = None) -> None:
        if kind not in KINDS:
            raise ValueError(f"unbekannte Fehlerart {kind}")
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.status = status
