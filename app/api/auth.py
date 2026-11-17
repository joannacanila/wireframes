"""/api/auth: admin sign-in, sign-out, current user."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.core.security import (
    _DUMMY,
    COOKIE,
    client_key,
    cookie_kwargs,
    create_session,
    current_admin,
    revoke_session,
    same_origin,
    verify_password,
)
from app.core.state import AppState, get_state
from app.models.orm import AdminUser

router = APIRouter(prefix="/api/auth", tags=["auth"])
State = Annotated[AppState, Depends(get_state)]


class Credentials(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


def _user_view(u: AdminUser) -> dict:
    role = getattr(u, "role", None) or "admin"
    return {"username": u.username, "display_name": u.display_name or u.username, "role": role,
            "can_admin": role == "admin"}


@router.post("/login")
def login(body: Credentials, request: Request, response: Response, st: State) -> dict:
    if not same_origin(request):
        raise HTTPException(403, {"code": "bad_origin", "message": "Cross-site sign-in refused."})
    key = f"{client_key(request)}|{body.username.lower()}"
    wait = st.login_limiter.blocked(key)
    if wait:
        raise HTTPException(429, {"code": "too_many_attempts",
                                  "message": f"Too many failed sign-ins. Try again in {int(wait // 60) + 1} min."},
                            headers={"Retry-After": str(int(wait))})
    with st.db.session() as s:
        user = s.scalar(select(AdminUser).where(AdminUser.username == body.username.strip().lower()))
        ok = verify_password(body.password, user.password_hash if user else _DUMMY) and user is not None and user.active
        if not ok:
            st.login_limiter.hit(key)
            raise HTTPException(status.HTTP_401_UNAUTHORIZED,
                                {"code": "bad_credentials", "message": "Username or password is incorrect."})
        user.last_login_at = datetime.now(UTC)
        uid, view = user.id, _user_view(user)
    st.login_limiter.reset(key)
    token = create_session(st.db, uid, request)
    response.set_cookie(value=token, **cookie_kwargs(st.settings.cookie_secure))
    return {"user": view}


@router.post("/logout", status_code=204)
def logout(request: Request, response: Response, st: State) -> None:
    token = request.cookies.get(COOKIE)
    if token:
        revoke_session(st.db, token)
    response.delete_cookie(COOKIE, path="/")


@router.get("/me")
def me(user=Depends(current_admin)) -> dict:
    """``{"user": null}`` when signed out: a normal state, not an error."""
    return {"user": None if user is None else _user_view(user)}
