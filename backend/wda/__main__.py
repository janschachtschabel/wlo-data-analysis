"""Start des Dienstes: ``python -m wda``. Bricht bei fehlerhafter Konfiguration mit Meldung ab."""

from __future__ import annotations

import os
import sys

import uvicorn

from .config import ConfigError
from .main import create_app


def main() -> int:
    try:
        app = create_app()
    except ConfigError as exc:
        print(f"Start abgebrochen: {exc}", file=sys.stderr)
        return 2
    # FORWARDED_ALLOW_IPS wertet uvicorn selbst aus (vertrauenswürdige Reverse-Proxys).
    uvicorn.run(
        app,
        host=os.environ.get("WDA_HOST", "0.0.0.0"),  # noqa: S104 - im Container gewollt
        port=int(os.environ.get("WDA_PORT", "8080")),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
