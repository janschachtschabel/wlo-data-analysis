"""Einrichtung vor dem Start im Container: ``python -m wda.init``.

Der erste Start soll ohne jede Variable gelingen. Fehlt ein Admin-Passwort, entsteht ein
zufälliges im Datenverzeichnis; die App liest es von dort (``WDA_ADMIN_PASSWORD_FILE``). Es
steht nie im Protokoll. Eine falsch geschriebene ``DOMAIN`` bricht hier mit klarer Meldung
ab, statt später als stiller Fehler im Proxy zu enden."""

from __future__ import annotations

import os
import re
import secrets
import sys
from pathlib import Path

PASSWORD_FILE = "admin.password"  # noqa: S105 - Dateiname, kein Geheimnis
_LABEL = r"[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
# die Endung beginnt mit einem Buchstaben und darf Punycode sein (xn--…)
_HOST = re.compile(rf"(?=.{{1,253}}$)({_LABEL}\.)+[A-Za-z]([A-Za-z0-9-]{{0,61}}[A-Za-z0-9])")


def main() -> int:
    domain = os.environ.get("DOMAIN", "").strip()
    if domain and not _HOST.fullmatch(domain):
        print("Einrichtung abgebrochen: DOMAIN ist der reine Hostname, etwa "
              "daten.example.org, ohne https://, Pfad oder Port.", file=sys.stderr)
        return 2
    if os.environ.get("WDA_ADMIN_PASSWORD", "").strip():
        print("Admin-Passwort: aus der Variable WDA_ADMIN_PASSWORD.")
        return 0
    data = Path(os.environ.get("WDA_DATA_DIR") or "/data")
    path = data / PASSWORD_FILE
    try:
        data.mkdir(parents=True, exist_ok=True)
        if not (path.exists() and path.read_text(encoding="utf-8").strip()):
            fd = os.open(path, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(secrets.token_urlsafe(24) + "\n")
            print(f"Admin-Passwort: neu erzeugt, es steht in {path} "
                  f"(anzeigen: docker compose exec app cat {path}). "
                  "Ein eigenes lässt sich mit WDA_ADMIN_PASSWORD setzen.")
        else:
            print(f"Admin-Passwort: aus {path}.")
        os.chmod(path, 0o600)
    except OSError as exc:
        print(f"Einrichtung abgebrochen: {path} lässt sich nicht schreiben "
              f"({type(exc).__name__}).", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
