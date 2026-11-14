from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel, StrictStr

from ..deps import is_admin
from ..security import COOKIE_NAME, verify_password

router = APIRouter(prefix="/auth")


class LoginBody(BaseModel):
    password: StrictStr


def _client_key(request: Request) -> str:
    return request.client.host if request.client else "unbekannt"


@router.post("/login", status_code=status.HTTP_204_NO_CONTENT)
def login(body: LoginBody, request: Request, response: Response) -> None:
    state = request.app.state
    key = _client_key(request)
    wait = state.throttle.retry_after(key)
    if wait:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Zu viele Fehlversuche, bitte später erneut versuchen",
            headers={"Retry-After": str(wait)},
        )
    if not verify_password(body.password, state.settings.admin_password.get_secret_value()):
        state.throttle.record_failure(key)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Passwort falsch")
    state.throttle.reset(key)
    response.set_cookie(
        COOKIE_NAME,
        state.signer.issue(),
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
def auth_status(request: Request) -> dict[str, bool]:
    return {"authenticated": is_admin(request)}
