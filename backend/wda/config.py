"""Konfiguration aus Umgebungsvariablen (Präfix ``WDA_``)."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, SecretStr, ValidationError, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class ConfigError(RuntimeError):
    """Die Konfiguration ist unvollständig oder fehlerhaft; die App startet nicht."""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="WDA_", extra="ignore")

    admin_password: SecretStr
    # optional: zweites Passwort nur zum Ansehen abgelegter Läufe und Berichte
    viewer_password: SecretStr | None = None
    session_secret: SecretStr | None = None
    session_max_age: int = 43200
    cookie_secure: bool = True
    data_dir: Path = Path("/data")
    instances: str | None = None
    llm_base_url: str = "https://api.openai.com/v1"
    llm_api_key: SecretStr | None = None
    llm_model: str = "gpt-6-luna"
    llm_effort: Literal["auto", "off", "low", "medium", "high"] = "auto"
    # ``on``: Adresse, Modell und Schlüssel lassen sich auch in der App eintragen
    llm_settings_ui: bool = False
    cache_ttl: float = Field(default=900.0, ge=0)
    vocab_ttl: float = Field(default=86400.0, ge=60)
    upstream_timeout: float = Field(default=90.0, gt=0)
    upstream_concurrency: int = Field(default=4, ge=1, le=32)
    # Materialien je Seite beim lokalen Abzug (gemessen: 1.000 ist am schnellsten)
    snapshot_page: int = Field(default=1000, ge=10, le=2000)
    # Uhrzeit (HH:MM in ``timezone``), zu der vorhandene Abzüge Änderungen nachziehen
    snapshot_nightly: str = "03:30"
    # Auswertungen, die jede Nacht zur selben Uhrzeit neu gezählt und abgelegt werden (IDs,
    # durch Komma getrennt); „off“: keine
    precompute: str = "builtin-themen"
    runs_keep: int = Field(default=500, ge=1, le=100000)
    # Versand von Berichten nach Zeitplan; ohne smtp_host gibt es keine E-Mails
    smtp_host: str = ""
    smtp_port: int = Field(default=587, ge=1, le=65535)
    smtp_user: str = ""
    smtp_password: SecretStr | None = None
    smtp_from: str = ""
    smtp_security: Literal["starttls", "ssl", "none"] = "starttls"
    # Adresse der App von außen (https://…), für den Link in Nachrichten
    public_url: str = ""
    oersi_url: str = "https://oersi.org/resources/api/search/oer_data/_search"
    timezone: str = "Europe/Berlin"  # in ihr gelten die Uhrzeiten der Zeitpläne
    scheduler: bool = True  # ``off``: Zeitpläne bleiben liegen (Tests, zweite Instanz)

    @field_validator("snapshot_nightly")
    @classmethod
    def _nightly_time(cls, value: str) -> str:
        value = value.strip().lower()
        if value in ("", "off", "aus"):
            return ""
        if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", value):
            raise ValueError("erwartet HH:MM oder off")
        return value

    @field_validator("timezone")
    @classmethod
    def _known_zone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError, OSError):
            raise ValueError("ist keine bekannte Zeitzone") from None
        return value

    @field_validator("admin_password")
    @classmethod
    def _password_not_blank(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value().strip():
            raise ValueError("darf nicht leer sein")
        return value

    @model_validator(mode="after")
    def _viewer_differs(self) -> Settings:
        if self.viewer_password is not None:
            viewer = self.viewer_password.get_secret_value()
            if not viewer.strip():
                raise ValueError("viewer_password darf nicht leer sein")
            if viewer == self.admin_password.get_secret_value():
                raise ValueError("viewer_password muss sich vom Admin-Passwort unterscheiden")
        return self

    @field_validator("llm_base_url", "public_url")
    @classmethod
    def _strip_slash(cls, value: str) -> str:
        return value.rstrip("/")


def _from_environment() -> dict[str, str]:
    """Was die Umgebung vorgibt, über die Felder hinaus: Leere Variablen gelten als nicht
    gesetzt (die mitgelieferte ``.env`` hat leere Felder), und das Admin-Passwort kann aus
    einer Datei kommen (``WDA_ADMIN_PASSWORD_FILE``), solange keines gesetzt ist."""
    for key, value in list(os.environ.items()):
        if key.startswith("WDA_") and not value.strip() and key != "WDA_ADMIN_PASSWORD":
            del os.environ[key]
    given: dict[str, str] = {}
    source = os.environ.get("WDA_ADMIN_PASSWORD_FILE", "").strip()
    if source and not os.environ.get("WDA_ADMIN_PASSWORD", "").strip():
        try:
            secret = Path(source).read_text(encoding="utf-8").strip()
        except OSError:
            secret = ""
        if secret:
            given["admin_password"] = secret
    return given


def load_settings() -> Settings:
    try:
        return Settings(**_from_environment())  # type: ignore[arg-type]
    except ValidationError as exc:
        fields = sorted({"WDA_" + str(err["loc"][0]).upper() for err in exc.errors()})
        hint = ""
        if "WDA_ADMIN_PASSWORD" in fields:
            hint = (
                " WDA_ADMIN_PASSWORD muss gesetzt sein,"
                " ohne Admin-Passwort startet die App nicht."
            )
        raise ConfigError(
            f"Ungültige Konfiguration: {', '.join(fields)}.{hint}"
        ) from None
