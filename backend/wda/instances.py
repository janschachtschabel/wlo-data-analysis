"""Verzeichnis der edu-sharing-Instanzen, gegen die ausgewertet wird."""

from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import urlsplit

from pydantic import (
    BaseModel,
    Field,
    SecretStr,
    ValidationError,
    field_validator,
    model_validator,
)

from .config import ConfigError

DEFAULT_FACET_FIELDS: tuple[str, ...] = (
    "ccm:commonlicense_key",
    "ccm:replicationsource",
    "ccm:oeh_publisher_combined",
    "cclom:general_language",
)
_LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
_FIELD_NAME = re.compile(r"^[A-Za-z0-9_:.\-]{1,200}$")
MAX_STORED = 30  # in der Oberfläche angelegte Instanzen


class Instance(BaseModel):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$", max_length=64)
    name: str = Field(min_length=1, max_length=200)
    base_url: str = Field(max_length=500)
    repository: str = Field(default="-home-", min_length=1, max_length=100)
    metadataset: str = Field(default="mds_oeh", min_length=1, max_length=100)
    query: str = Field(default="ngsearch", min_length=1, max_length=100)
    search_url: str | None = Field(default=None, max_length=500)
    username: str | None = Field(default=None, max_length=200)
    password: SecretStr | None = None
    # Felder ohne Werteliste im Metadatenset, die trotzdem als Facette erlaubt sind.
    # Alles andere ohne Werteliste (Titel, URL, …) wäre eine Datensatzliste.
    facet_fields: tuple[str, ...] = DEFAULT_FACET_FIELDS
    # Einwertige Felder, bei denen ein Kriterium als Präfix trifft und nur einen Wert
    # annimmt (gemessen: der Lizenzschlüssel, "CC_BY" zählt alle "CC_BY…").
    prefix_fields: tuple[str, ...] = ("ccm:commonlicense_key",)

    @field_validator("name")
    @classmethod
    def _name_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("darf nicht leer sein")
        return value.strip()

    @field_validator("facet_fields", "prefix_fields")
    @classmethod
    def _field_names(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) > 100 or not all(_FIELD_NAME.match(name) for name in value):
            raise ValueError("erwartet höchstens 100 Feldnamen wie 'ccm:commonlicense_key'")
        return value

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

    @model_validator(mode="after")
    def _credentials_need_https(self) -> Instance:
        parts = urlsplit(self.base_url)
        if (self.username or self.password) and parts.scheme != "https" \
                and parts.hostname not in _LOCAL_HOSTS:
            raise ValueError("Zugangsdaten sind nur mit einer https-URL erlaubt")
        return self

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
    """Feste Instanzen (mitgeliefert oder aus ``WDA_INSTANCES``) und solche, die in der
    Oberfläche angelegt wurden. Eine angelegte mit der ID einer festen überschreibt sie."""

    def __init__(self, extra_json: str | None) -> None:
        self._fixed: dict[str, Instance] = {i.id: i for i in PRESETS}
        self._source: dict[str, str] = {i.id: "preset" for i in PRESETS}
        for inst in self._parse(extra_json):
            self._fixed[inst.id] = inst
            self._source[inst.id] = "config"
        self._stored: dict[str, Instance] = {}

    def set_stored(self, items: list[Instance]) -> None:
        self._stored = {inst.id: inst for inst in items}

    def put(self, inst: Instance) -> None:
        self._stored[inst.id] = inst

    def remove(self, instance_id: str) -> None:
        self._stored.pop(instance_id, None)

    def is_stored(self, instance_id: str) -> bool:
        return instance_id in self._stored

    def is_fixed(self, instance_id: str) -> bool:
        return instance_id in self._fixed

    def stored_count(self) -> int:
        return len(self._stored)

    def describe(self, inst: Instance) -> dict[str, Any]:
        """Für die Oberfläche: ohne Zugangsdaten, mit Herkunft."""
        stored = inst.id in self._stored
        return {**inst.public(),
                "source": "stored" if stored else self._source[inst.id],
                "overridden": stored and inst.id in self._fixed}

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
        fixed = [self._stored.get(key, inst) for key, inst in self._fixed.items()]
        return fixed + [inst for key, inst in self._stored.items() if key not in self._fixed]

    def get(self, instance_id: str) -> Instance:
        """Wirft ``KeyError`` bei unbekannter Instanz."""
        found = self._stored.get(instance_id)
        return found if found is not None else self._fixed[instance_id]
