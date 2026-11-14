"""Welches Sprachmodell benutzt wird. Werte aus der Oberfläche gehen vor denen der Umgebung."""

from __future__ import annotations

import ipaddress
import json
import re
from dataclasses import dataclass
from typing import Annotated, Any, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, SecretStr, StrictStr, field_validator

from ..config import Settings

Effort = Literal["auto", "off", "low", "medium", "high"]
META_KEY = "llm"
# Modellfamilien, die ``reasoning_effort`` kennen (gemessen an der B-API und an OpenAI)
_REASONING = ("gpt-5", "gpt-6", "o1", "o3", "o4")


def check_base_url(value: str) -> str:
    """Die Basis-URL ohne Schrägstrich am Ende. ``http`` nur für Rechner im eigenen Netz
    (localhost, private Adressen, Namen ohne Punkt), damit der Schlüssel nie offen reist."""
    value = value.strip().rstrip("/")
    if re.search(r"[\s\x00-\x1f\x7f]", value):
        raise ValueError("darf keine Leer- oder Steuerzeichen enthalten")
    try:
        parts = urlsplit(value)
        _ = parts.port
    except ValueError:
        raise ValueError("muss eine gültige Adresse sein (Rechner und Port)") from None
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError("muss mit https:// beginnen und einen Rechner nennen")
    if parts.username or parts.password or "@" in parts.netloc:
        raise ValueError("darf keine Zugangsdaten enthalten")
    if parts.query or parts.fragment:
        raise ValueError("darf weder ? noch # enthalten")
    if parts.scheme == "http" and not _local(parts.hostname):
        raise ValueError("muss https benutzen (http nur im eigenen Netz)")
    return value


def _local(host: str) -> bool:
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return host == "localhost" or "." not in host
    return address.is_loopback or address.is_private


def host_of(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{(parts.hostname or '').lower()}:{parts.port or ''}"


@dataclass(frozen=True)
class LlmConfig:
    base_url: str
    model: str
    api_key: str | None
    effort: str = "auto"
    stored: bool = False  # in der Oberfläche gesetzt (sonst aus der Umgebung)

    @property
    def ready(self) -> bool:
        return bool(self.api_key) or _local(urlsplit(self.base_url).hostname or "")

    @property
    def reasoning_effort(self) -> str | None:
        if self.effort == "off":
            return None
        if self.effort == "auto":
            return "low" if self.model.lower().startswith(_REASONING) else None
        return self.effort

    def public(self) -> dict[str, Any]:
        """Für die Oberfläche: nie der Schlüssel, nur ob einer gesetzt ist."""
        return {"base_url": self.base_url, "model": self.model, "effort": self.effort,
                "api_key_set": bool(self.api_key), "ready": self.ready, "stored": self.stored}


class LlmIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    base_url: Annotated[StrictStr, Field(min_length=8, max_length=300)]
    model: Annotated[StrictStr, Field(min_length=1, max_length=120, pattern=r"^[^\s\x00-\x1f]+$")]
    api_key: SecretStr | None = Field(default=None, max_length=500)
    effort: Effort = "auto"

    @field_validator("base_url")
    @classmethod
    def _url(cls, value: str) -> str:
        return check_base_url(value)

    @field_validator("api_key")
    @classmethod
    def _key(cls, value: SecretStr | None) -> SecretStr | None:
        if value is None:
            return None
        text = value.get_secret_value().strip()
        if any(ord(ch) < 33 or ord(ch) > 126 for ch in text):
            raise ValueError("enthält unerlaubte Zeichen")
        return SecretStr(text) if text else None


def from_env(settings: Settings) -> LlmConfig:
    key = settings.llm_api_key.get_secret_value().strip() if settings.llm_api_key else ""
    return LlmConfig(settings.llm_base_url, settings.llm_model, key or None)


def from_stored(text: str | None, settings: Settings) -> LlmConfig:
    """Die gespeicherten Werte; Unlesbares fällt auf die Umgebung zurück."""
    if not text:
        return from_env(settings)
    try:
        data = json.loads(text)
        checked = LlmIn(base_url=data["base_url"], model=data["model"],
                        effort=data.get("effort", "auto"))
        key = data.get("api_key")
        return LlmConfig(checked.base_url, checked.model,
                         key if isinstance(key, str) and key else None, checked.effort, True)
    except (ValueError, KeyError, TypeError):
        return from_env(settings)


def to_stored(config: LlmConfig) -> str:
    return json.dumps({"base_url": config.base_url, "model": config.model,
                       "api_key": config.api_key, "effort": config.effort})
