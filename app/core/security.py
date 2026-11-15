"""Admin authentication and request guards.

* Passwords: scrypt (RFC 7914, N=2^14, r=8, p=1) with a 16-byte salt, standard library only.
* Sessions: a 256-bit random token in an HttpOnly, SameSite=Strict cookie. The database keeps
  only its SHA-256, so a leaked database does not leak live sessions. Sessions expire after
  ``SESSION_HOURS`` and are revoked on logout.
* CSRF: SameSite=Strict keeps the cookie off cross-site requests; as defence in depth, every
  state-changing admin request must also carry an Origin (or Referer) matching the host.
* Throttling: login failures per (client, username) and anonymous uploads per client are
  limited in memory (single-process deployment, which is what the queue design assumes).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import threading
import time
from collections import defaultdict, deque
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select

COOKIE = "wf_admin"
SESSION_HOURS = 8
_N, _R, _P = 2**14, 8, 1
MIN_PASSWORD = 12


# ----------------------------------------------------------------------------- passwords


def hash_password(password: str) -> str:
    if len(password) < MIN_PASSWORD:
        raise ValueError(f"password must be at least {MIN_PASSWORD} characters")
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P, dklen=32)
    b64 = lambda b: base64.b64encode(b).decode()  # noqa: E731
    return f"scrypt${_N}${_R}${_P}${b64(salt)}${b64(digest)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, n, r, p, salt, digest = stored.split("$")
        if algo != "scrypt":
            return False
        got = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt), n=int(n), r=int(r), p=int(p),
                             dklen=len(base64.b64decode(digest)))
        return hmac.compare_digest(got, base64.b64decode(digest))
    except (ValueError, TypeError):
        return False


# A fixed hash used when the username does not exist, so a failed login costs the same time
# whether or not the account exists (no username enumeration by timing).
_DUMMY = hash_password("x" * MIN_PASSWORD)


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


# ----------------------------------------------------------------------------- throttling


class SlidingWindow:
    def __init__(self, limit: int, seconds: float) -> None:
        self.limit, self.seconds = limit, seconds
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def _trim(self, key: str, now: float) -> deque[float]:
        q = self._hits[key]
        while q and now - q[0] > self.seconds:
            q.popleft()
        return q

    def blocked(self, key: str) -> float:
        """Seconds until the key may try again (0 when allowed)."""
        now = time.monotonic()
        with self._lock:
            q = self._trim(key, now)
            return 0.0 if len(q) < self.limit else max(1.0, self.seconds - (now - q[0]))

    def hit(self, key: str) -> None:
        with self._lock:
            self._trim(key, time.monotonic()).append(time.monotonic())

    def reset(self, key: str) -> None:
        with self._lock:
            self._hits.pop(key, None)


def client_key(request: Request) -> str:
    return request.client.host if request.client else "unknown"


# ----------------------------------------------------------------------------- sessions


def create_session(db, user_id: int, request: Request) -> str:
    from app.models.orm import AdminSession

    token = secrets.token_urlsafe(32)
    now = datetime.now(UTC)
    with db.session() as s:
        s.add(AdminSession(token_sha256=token_digest(token), user_id=user_id, created_at=now,
                           expires_at=now + timedelta(hours=SESSION_HOURS),
                           user_agent=(request.headers.get("user-agent") or "")[:200]))
    return token


def revoke_session(db, token: str) -> None:
    from app.models.orm import AdminSession

    with db.session() as s:
        row = s.get(AdminSession, token_digest(token))
        if row is not None:
            s.delete(row)


def session_user(db, token: str | None):
    """The AdminUser for a live session token, or None. Expired sessions are deleted."""
    if not token:
        return None
    from app.models.orm import AdminSession, AdminUser

    with db.session() as s:
        row = s.get(AdminSession, token_digest(token))
        if row is None:
            return None
        if row.expires_at <= datetime.now(UTC):
            s.delete(row)
            return None
        user = s.scalar(select(AdminUser).where(AdminUser.id == row.user_id, AdminUser.active.is_(True)))
        if user is None:
            return None
        s.expunge(user)
        return user


def same_origin(request: Request) -> bool:
    origin = request.headers.get("origin") or request.headers.get("referer")
    if not origin:
        return False
    host = request.headers.get("host", "")
    return urlsplit(origin).netloc == host


def current_admin(request: Request):
    """The signed-in admin or None (for routes that behave differently when signed in)."""
    st = request.app.state.wf
    return session_user(st.db, request.cookies.get(COOKIE))


def require_admin(request: Request, user=Depends(current_admin)):
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED,
                            {"code": "auth_required", "message": "Sign in to the admin portal to continue."})
    if request.method not in {"GET", "HEAD", "OPTIONS"} and not same_origin(request):
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            {"code": "bad_origin", "message": "Cross-site request refused."})
    return user


def cookie_kwargs(secure: bool) -> dict:
    return dict(key=COOKIE, httponly=True, samesite="strict", secure=secure, path="/",
                max_age=SESSION_HOURS * 3600)
