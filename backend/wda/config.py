"""Konfiguration aus Umgebungsvariablen (Präfix ``WDA_``)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, SecretStr, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class ConfigError(RuntimeError):
    """Die Konfiguration ist unvollständig oder fehlerhaft; die App startet nicht."""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="WDA_", extra="ignore")

    admin_password: SecretStr
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
    runs_keep: int = Field(default=500, ge=1, le=100000)
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

    @field_validator("llm_base_url")
    @classmethod
    def _strip_slash(cls, value: str) -> str:
        return value.rstrip("/")


def load_settings() -> Settings:
    try:
        return Settings()
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
