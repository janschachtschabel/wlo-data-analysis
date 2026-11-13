"""Verzeichnis der edu-sharing-Instanzen, gegen die ausgewertet wird."""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, SecretStr, ValidationError, field_validator

from .config import ConfigError


class Instance(BaseModel):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$", max_length=64)
    name: str = Field(min_length=1)
    base_url: str
    repository: str = "-home-"
    metadataset: str = "mds_oeh"
    query: str = "ngsearch"
    search_url: str | None = None
    username: str | None = None
    password: SecretStr | None = None

    @field_validator("base_url", "search_url")
    @classmethod
    def _http_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        parts = urlsplit(value)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise ValueError("muss eine http(s)-URL mit Hostnamen sein")
        if parts.username or parts.password or "@" in parts.netloc:
            raise ValueError("darf keine Zugangsdaten enthalten (username/password nutzen)")
        if parts.query or parts.fragment:
            raise ValueError("darf weder Abfrage-Teil noch Fragment enthalten")
        return value.rstrip("/")

    @property
    def rest_url(self) -> str:
        return f"{self.base_url}/edu-sharing/rest"

    def public(self) -> dict[str, Any]:
        """Darstellung für die Oberfläche: nie mit Zugangsdaten."""
        data = self.model_dump(exclude={"username", "password"})
        data["has_credentials"] = bool(self.username and self.password)
        return data


PRESETS: tuple[Instance, ...] = (
    Instance(
        id="wlo-prod",
        name="WLO Produktion",
        base_url="https://redaktion.openeduhub.net",
        search_url="https://suche.wirlernenonline.de",
    ),
    Instance(
        id="wlo-staging",
        name="WLO Staging",
        base_url="https://repository.staging.openeduhub.net",
    ),
)


class InstanceRegistry:
    def __init__(self, extra_json: str | None) -> None:
        self._items: dict[str, Instance] = {i.id: i for i in PRESETS}
        for inst in self._parse(extra_json):
            self._items[inst.id] = inst

    @staticmethod
    def _parse(extra_json: str | None) -> list[Instance]:
        if not extra_json or not extra_json.strip():
            return []
        try:
            raw = json.loads(extra_json)
        except json.JSONDecodeError as exc:
            raise ConfigError(f"WDA_INSTANCES ist kein gültiges JSON: {exc.msg}") from None
        if not isinstance(raw, list):
            raise ConfigError("WDA_INSTANCES muss eine JSON-Liste von Instanzen sein.")
        result: list[Instance] = []
        seen: set[str] = set()
        for pos, entry in enumerate(raw, 1):
            try:
                inst = Instance.model_validate(entry)
            except ValidationError as exc:
                problems = "; ".join(
                    f"{'.'.join(str(p) for p in e['loc']) or 'Eintrag'}: {e['msg']}"
                    for e in exc.errors(include_input=False)
                )
                raise ConfigError(f"WDA_INSTANCES, Eintrag {pos}: {problems}") from None
            if inst.id in seen:
                raise ConfigError(f"WDA_INSTANCES enthält die ID '{inst.id}' mehrfach.")
            seen.add(inst.id)
            result.append(inst)
        return result

    def list(self) -> list[Instance]:
        return list(self._items.values())

    def get(self, instance_id: str) -> Instance:
        return self._items[instance_id]
