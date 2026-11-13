"""Instanzen: Liste für die Oberfläche, Anlegen, Ändern, Entfernen. Zugangsdaten gehen nur
hinein, nie heraus."""

from __future__ import annotations

from typing import Annotated, Any
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, SecretStr, StrictStr, ValidationError

from ..errors import UnknownInstance, describe_validation, error_body
from ..instances import MAX_STORED, Instance

router = APIRouter(prefix="/instances")
Short = Annotated[StrictStr, Field(max_length=500)]


class InstanceBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Short
    base_url: Short
    repository: Short | None = None
    metadataset: Short | None = None
    query: Short | None = None
    search_url: Short | None = None
    username: Short | None = None
    # Weggelassen heißt beim Ändern: das bisherige Passwort behalten.
    password: SecretStr | None = Field(default=None, max_length=500)
    # Die Oberfläche kennt den Benutzernamen nicht: So bleibt die gespeicherte Anmeldung.
    keep_credentials: bool = False
    facet_fields: list[Short] | None = Field(default=None, max_length=100)
    prefix_fields: list[Short] | None = Field(default=None, max_length=100)


class InstanceCreate(InstanceBody):
    id: StrictStr = Field(pattern=r"^[a-z0-9][a-z0-9-]*$", max_length=64)


class _Invalid(Exception):
    pass


def _origin(url: str) -> tuple[str, str | None, int | None]:
    parts = urlsplit(url)
    return parts.scheme, parts.hostname, parts.port


def _build(instance_id: str, body: InstanceBody, previous: Instance | None) -> Instance:
    data = body.model_dump(exclude_none=True, exclude={"id", "keep_credentials"})
    username = (body.username or "").strip() or None
    password = body.password if body.password and body.password.get_secret_value() else None
    data.pop("username", None)
    data.pop("password", None)
    if password and not username:
        raise _Invalid("Ungültig: 'username' – zum Passwort fehlt der Benutzername.")
    keep = body.keep_credentials and not username and previous is not None \
        and previous.username is not None and previous.password is not None
    if keep:
        username = previous.username
    if username and not password:
        kept = previous.password if previous and previous.username == username else None
        if kept is None:
            raise _Invalid("Ungültig: 'password' – zum Benutzernamen fehlt das Passwort.")
        try:
            same = _origin(previous.base_url) == _origin(body.base_url.rstrip("/"))
        except ValueError:
            same = False
        if not same:
            # Ein gespeichertes Passwort wandert nicht stillschweigend an eine andere Adresse.
            raise _Invalid("Ungültig: 'password' – für eine andere Adresse muss das Passwort "
                           "neu eingegeben werden.")
        password = kept
    try:
        return Instance(id=instance_id, username=username, password=password, **data)
    except ValidationError as exc:
        raise _Invalid(describe_validation(exc.errors(include_input=False))) from None


def _invalid(exc: _Invalid) -> JSONResponse:
    return JSONResponse(error_body("invalid_request", str(exc)), status_code=422)


@router.get("")
def list_instances(request: Request) -> list[dict[str, Any]]:
    registry = request.app.state.services.registry
    return [registry.describe(inst) for inst in registry.list()]


@router.post("", status_code=201)
async def create_instance(body: InstanceCreate, request: Request) -> Any:
    services = request.app.state.services
    registry = services.registry
    if registry.is_stored(body.id) or registry.is_fixed(body.id):
        raise HTTPException(409, f"Die Instanz '{body.id}' gibt es schon.")
    if registry.stored_count() >= MAX_STORED:
        raise HTTPException(409, f"Es lassen sich höchstens {MAX_STORED} Instanzen anlegen.")
    try:
        inst = _build(body.id, body, None)
    except _Invalid as exc:
        return _invalid(exc)
    await services.save_instance(inst)
    return registry.describe(inst)


@router.put("/{instance_id}")
async def update_instance(instance_id: str, body: InstanceBody, request: Request) -> Any:
    """Ändert eine angelegte Instanz; bei einer festen entsteht eine Fassung, die sie
    überschreibt (Entfernen stellt die feste wieder her)."""
    services = request.app.state.services
    registry = services.registry
    try:
        previous = registry.get(instance_id)
    except KeyError:
        raise UnknownInstance(instance_id) from None
    if not registry.is_stored(instance_id) and registry.stored_count() >= MAX_STORED:
        raise HTTPException(409, f"Es lassen sich höchstens {MAX_STORED} Instanzen anlegen.")
    try:
        inst = _build(instance_id, body, previous)
    except _Invalid as exc:
        return _invalid(exc)
    await services.save_instance(inst)
    return registry.describe(inst)


@router.delete("/{instance_id}", status_code=204)
async def delete_instance(instance_id: str, request: Request) -> Response:
    services = request.app.state.services
    registry = services.registry
    if not registry.is_stored(instance_id):
        if registry.is_fixed(instance_id):
            raise HTTPException(409, "Diese Instanz ist fest eingerichtet und lässt sich "
                                     "nicht entfernen.")
        raise UnknownInstance(instance_id)
    await services.delete_instance(instance_id)
    return Response(status_code=204)
