"""Start des Dienstes: ``python -m wda``. Bricht bei fehlerhafter Konfiguration mit Meldung ab."""

from __future__ import annotations

import os
import sys

import uvicorn

from .config import ConfigError
from .main import create_app


def _port(value: str | None) -> int:
    """Port aus ``WDA_PORT``; leer heißt 8080."""
    text = (value or "").strip() or "8080"
    if not text.isdigit() or not 1 <= int(text) <= 65535:
        raise ConfigError(f"WDA_PORT ist kein Port (1 bis 65535): {text[:20]!r}")
    return int(text)


def main() -> int:
    try:
        port = _port(os.environ.get("WDA_PORT"))
        app = create_app()
        # die Ablage schon hier öffnen: Eine beschädigte Datei beendet den Start mit einem
        # Satz statt mit einer Fehlerkette beim Hochfahren
        _ = app.state.services.db
    except ConfigError as exc:
        print(f"Start abgebrochen: {exc}", file=sys.stderr)
        return 2
    # FORWARDED_ALLOW_IPS wertet uvicorn selbst aus (vertrauenswürdige Reverse-Proxys).
    uvicorn.run(
        app,
        host=os.environ.get("WDA_HOST", "0.0.0.0"),  # noqa: S104 - im Container gewollt
        port=port,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
