"""Dashboard login: one admin account, signed long-lived session cookie.

* Username defaults to ``admin`` (ADMIN_USERNAME), the password comes from ADMIN_PASSWORD in ``.env``.
* A successful login sets an HttpOnly cookie signed with HMAC-SHA256. It is stateless, so it survives
  backend restarts; the browser stays logged in until the cookie expires (AUTH_SESSION_DAYS) or the
  user logs out. Changing the password (or AUTH_SECRET) invalidates every existing session.
* ADMIN_PASSWORD empty = login disabled (previous behaviour, e.g. for tests).
* Failed logins are rate limited per client IP.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import threading
import time
from dataclasses import dataclass

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from app.config.settings import Settings

COOKIE_NAME = "polybot_session"
MAX_FAILURES = 5
LOCK_BASE_SECONDS = 30
LOCK_MAX_SECONDS = 15 * 60
FAILURE_WINDOW_SECONDS = 15 * 60


class LoginRequest(BaseModel):
    username: str = Field(default="", max_length=128)
    password: str = Field(default="", max_length=512)


@dataclass
class _Attempts:
    failures: int = 0
    first_at: float = 0.0
    locked_until: float = 0.0
    lock_level: int = 0


class AuthService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._lock = threading.Lock()
        self._attempts: dict[str, _Attempts] = {}

    # ---- configuration -------------------------------------------------
    @property
    def enabled(self) -> bool:
        return bool(self.settings.admin_password)

    @property
    def username(self) -> str:
        return self.settings.admin_username.strip() or "admin"

    @property
    def session_seconds(self) -> int:
        return max(1, int(self.settings.auth_session_days)) * 86400

    def _key(self) -> bytes:
        secret = self.settings.auth_secret.strip()
        material = f"polybot-session|{secret}|{self.username}|{self.settings.admin_password}"
        return hashlib.sha256(material.encode()).digest()

    # ---- credentials ---------------------------------------------------
    def check_credentials(self, username: str, password: str) -> bool:
        user_ok = hmac.compare_digest(username.strip().lower().encode(), self.username.lower().encode())
        pass_ok = hmac.compare_digest(password.encode(), self.settings.admin_password.encode())
        return user_ok and pass_ok

    # ---- session tokens ------------------------------------------------
    def issue_token(self) -> str:
        payload = {"u": self.username, "exp": int(time.time()) + self.session_seconds}
        body = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode().rstrip("=")
        sig = hmac.new(self._key(), body.encode(), hashlib.sha256).hexdigest()
        return f"{body}.{sig}"

    def verify_token(self, token: str | None) -> bool:
        if not token or "." not in token:
            return False
        body, _, sig = token.rpartition(".")
        expected = hmac.new(self._key(), body.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig.encode(), expected.encode()):
            return False
        try:
            padded = body + "=" * (-len(body) % 4)
            payload = json.loads(base64.urlsafe_b64decode(padded.encode()))
        except Exception:
            return False
        return payload.get("u") == self.username and int(payload.get("exp", 0)) > time.time()

    # ---- request helpers -----------------------------------------------
    def request_authenticated(self, request_cookies: dict[str, str]) -> bool:
        return self.verify_token(request_cookies.get(COOKIE_NAME))

    def set_cookie(self, response: Response, request: Request) -> None:
        proto = request.headers.get("x-forwarded-proto", request.url.scheme)
        response.set_cookie(
            COOKIE_NAME,
            self.issue_token(),
            max_age=self.session_seconds,
            httponly=True,
            samesite="lax",
            secure=proto == "https",
            path="/",
        )

    @staticmethod
    def clear_cookie(response: Response) -> None:
        response.delete_cookie(COOKIE_NAME, path="/")

    # ---- brute-force guard ---------------------------------------------
    @staticmethod
    def client_id(request: Request) -> str:
        return request.client.host if request.client else "unknown"

    def retry_after(self, client: str) -> int:
        with self._lock:
            state = self._attempts.get(client)
            if not state:
                return 0
            remaining = state.locked_until - time.time()
            return int(remaining) + 1 if remaining > 0 else 0

    def record_failure(self, client: str) -> int:
        now = time.time()
        with self._lock:
            state = self._attempts.setdefault(client, _Attempts(first_at=now))
            if now - state.first_at > FAILURE_WINDOW_SECONDS and state.locked_until < now:
                state.failures, state.first_at, state.lock_level = 0, now, 0
            state.failures += 1
            if state.failures >= MAX_FAILURES:
                state.lock_level += 1
                wait = min(LOCK_BASE_SECONDS * 2 ** (state.lock_level - 1), LOCK_MAX_SECONDS)
                state.locked_until = now + wait
                state.failures = 0
                return int(wait)
            return 0

    def record_success(self, client: str) -> None:
        with self._lock:
            self._attempts.pop(client, None)


def build_auth_router(auth: AuthService) -> APIRouter:
    router = APIRouter(prefix="/api/auth")

    @router.get("/me")
    async def me(request: Request, response: Response) -> dict:
        if not auth.enabled:
            return {"auth_required": False, "authenticated": True, "username": auth.username}
        ok = auth.request_authenticated(request.cookies)
        if ok:
            auth.set_cookie(response, request)  # sliding expiry: active browsers never get logged out
        return {"auth_required": True, "authenticated": ok, "username": auth.username if ok else None}

    @router.post("/login")
    async def login(body: LoginRequest, request: Request, response: Response) -> dict:
        if not auth.enabled:
            return {"auth_required": False, "authenticated": True, "username": auth.username}
        client = auth.client_id(request)
        wait = auth.retry_after(client)
        if wait:
            raise HTTPException(
                status_code=429,
                detail=f"Too many failed attempts. Try again in {wait}s.",
                headers={"Retry-After": str(wait)},
            )
        if not auth.check_credentials(body.username, body.password):
            lock = auth.record_failure(client)
            if lock:
                raise HTTPException(
                    status_code=429,
                    detail=f"Too many failed attempts. Locked for {lock}s.",
                    headers={"Retry-After": str(lock)},
                )
            raise HTTPException(status_code=401, detail="Incorrect username or password.")
        auth.record_success(client)
        auth.set_cookie(response, request)
        return {"auth_required": True, "authenticated": True, "username": auth.username}

    @router.post("/logout")
    async def logout(response: Response) -> dict:
        auth.clear_cookie(response)
        return {"auth_required": auth.enabled, "authenticated": False, "username": None}

    return router
