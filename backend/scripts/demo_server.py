"""Startet das Backend mit einem nachgebauten Repositorium (Testbestand) statt WLO.

Für die Entwicklung und die Durchstiche der Oberfläche; nie für den Betrieb.
Aufruf:  python scripts/demo_server.py [port]      Passwort: demo-passwort
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import uvicorn

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.esdata import DataRepo  # noqa: E402
from wda.config import Settings  # noqa: E402
from wda.main import create_app  # noqa: E402

PASSWORD = "demo-passwort"  # noqa: S105 (nur Demo mit nachgebautem Bestand, nie Betrieb)


def main() -> None:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    settings = Settings(admin_password=PASSWORD, cookie_secure=False,
                        data_dir=Path(tempfile.mkdtemp(prefix="wda-demo-")))
    app = create_app(settings, transport=DataRepo().transport())
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main()
