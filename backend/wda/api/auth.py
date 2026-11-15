from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel, StrictStr

from ..deps import role_of
from ..security import COOKIE_NAME, verify_password

router = APIRouter(prefix="/auth")


class LoginBody(BaseModel):
    password: StrictStr


EVERYONE = "*"


def _client_key(request: Request) -> str:
    return request.client.host if request.client else "unbekannt"


@router.post("/login", status_code=status.HTTP_204_NO_CONTENT)
def login(body: LoginBody, request: Request, response: Response) -> None:
    state = request.app.state
    key = _client_key(request)
    # je Adresse und, falls die Adresse gefälscht ist (X-Forwarded-For), über alle zusammen
    wait = max(state.throttle.retry_after(key), state.throttle_all.retry_after(EVERYONE))
    if wait:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Zu viele Fehlversuche, bitte später erneut versuchen",
            headers={"Retry-After": str(wait)},
        )
    viewer = state.settings.viewer_password
    if verify_password(body.password, state.settings.admin_password.get_secret_value()):
        role = "admin"
    elif viewer is not None and verify_password(body.password, viewer.get_secret_value()):
        role = "viewer"
    else:
        state.throttle.record_failure(key)
        state.throttle_all.record_failure(EVERYONE)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Passwort falsch")
    state.throttle.reset(key)
    response.set_cookie(
        COOKIE_NAME,
        state.signer.issue(role),
        max_age=state.settings.session_max_age,
        httponly=True,
        samesite="strict",
        secure=state.settings.cookie_secure,
        path="/",
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(request: Request, response: Response) -> None:
    response.delete_cookie(
        COOKIE_NAME,
        httponly=True,
        samesite="strict",
        secure=request.app.state.settings.cookie_secure,
        path="/",
    )


@router.get("/status")
def auth_status(request: Request) -> dict[str, Any]:
    role = role_of(request)
    return {"authenticated": True, "role": role} if role else {"authenticated": False}
