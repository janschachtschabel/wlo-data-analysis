"""Admin-Session, Passwortvergleich und Login-Drossel."""

from __future__ import annotations

import hashlib
import hmac
import logging
import math
import os
import secrets
import time
from collections import deque
from collections.abc import Callable

from itsdangerous import BadData, URLSafeTimedSerializer

from .config import Settings

log = logging.getLogger("wda.security")

COOKIE_NAME = "wda_session"
_SECRET_FILE = "session.secret"  # noqa: S105 - Dateiname, kein Geheimnis


class SessionSigner:
    """Stellt signierte, zeitlich begrenzte Session-Tokens aus und prüft sie."""

    def __init__(self, secret: str, max_age: int) -> None:
        self._serializer = URLSafeTimedSerializer(secret, salt="wda-admin-session")
        self._max_age = max_age

    def issue(self) -> str:
        return self._serializer.dumps({"role": "admin", "n": secrets.token_hex(8)})

    def valid(self, token: str | None) -> bool:
        if not token or self._max_age <= 0:
            return False
        try:
            data = self._serializer.loads(token, max_age=self._max_age)
        except BadData:
            return False
        return isinstance(data, dict) and data.get("role") == "admin"


def resolve_session_secret(settings: Settings) -> str:
    """Geheimnis aus der Umgebung, sonst dauerhaft im Datenverzeichnis, sonst nur im Speicher."""
    if settings.session_secret is not None and settings.session_secret.get_secret_value():
        return settings.session_secret.get_secret_value()
    path = settings.data_dir / _SECRET_FILE
    try:
        existing = path.read_text(encoding="utf-8").strip()
        if len(existing) >= 32:
            return existing
    except OSError:
        pass
    secret = secrets.token_urlsafe(48)
    try:
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(secret)
        os.chmod(path, 0o600)
    except OSError as exc:
        log.warning(
            "Session-Geheimnis konnte nicht in %s abgelegt werden (%s). "
            "Anmeldungen gelten nur bis zum nächsten Neustart.",
            path,
            exc.__class__.__name__,
        )
    return secret


def session_signing_key(settings: Settings) -> str:
    """Signaturschlüssel, der am Admin-Passwort hängt.

    Ein neues Passwort entwertet damit alle bestehenden Sessions.
    """
    password = settings.admin_password.get_secret_value().encode("utf-8", "surrogatepass")
    secret = resolve_session_secret(settings).encode("utf-8")
    return hmac.new(secret, b"wda-session-v1:" + password, hashlib.sha256).hexdigest()


def verify_password(given: str, expected: str) -> bool:
    # surrogatepass: auch kaputte Unicode-Eingaben sind nur ein falsches Passwort, kein Fehler
    return hmac.compare_digest(
        given.encode("utf-8", "surrogatepass"), expected.encode("utf-8", "surrogatepass")
    )


class LoginThrottle:
    """Sperrt einen Schlüssel nach ``limit`` Fehlversuchen innerhalb von ``window`` Sekunden."""

    def __init__(
        self,
        limit: int = 5,
        window: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
        max_keys: int = 10_000,
    ) -> None:
        self._limit = limit
        self._window = window
        self._clock = clock
        self._max_keys = max_keys
        self._failures: dict[str, deque[float]] = {}

    def _recent(self, key: str) -> deque[float]:
        entries = self._failures.get(key)
        if entries is None:
            return deque()
        cutoff = self._clock() - self._window
        while entries and entries[0] <= cutoff:
            entries.popleft()
        if not entries:
            del self._failures[key]
        return entries

    def retry_after(self, key: str) -> int:
        """0, wenn ein Versuch erlaubt ist, sonst die Wartezeit in Sekunden."""
        entries = self._recent(key)
        if len(entries) < self._limit:
            return 0
        return max(1, math.ceil(entries[0] + self._window - self._clock()))

    def allow(self, key: str) -> bool:
        return self.retry_after(key) == 0

    def record_failure(self, key: str) -> None:
        if key not in self._failures and len(self._failures) >= self._max_keys:
            for stale in list(self._failures):
                self._recent(stale)
            if len(self._failures) >= self._max_keys:
                self._failures.pop(next(iter(self._failures)))
        self._failures.setdefault(key, deque()).append(self._clock())

    def reset(self, key: str) -> None:
        self._failures.pop(key, None)
