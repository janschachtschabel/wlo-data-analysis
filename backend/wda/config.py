"""Konfiguration aus Umgebungsvariablen (Präfix ``WDA_``)."""

from __future__ import annotations

from pathlib import Path

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
    cache_ttl: float = Field(default=900.0, ge=0)
    vocab_ttl: float = Field(default=86400.0, ge=0)
    upstream_timeout: float = Field(default=90.0, gt=0)
    upstream_concurrency: int = Field(default=4, ge=1, le=32)

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
