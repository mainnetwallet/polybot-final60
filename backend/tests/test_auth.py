from __future__ import annotations

import time

from fastapi.testclient import TestClient

from app.api.auth import COOKIE_NAME, MAX_FAILURES, AuthService
from app.config.settings import Settings
from app.main import create_app


def _client(password: str = "s3cret-pass", **extra) -> TestClient:
    app = create_app()
    settings = app.state.settings
    original = (settings.admin_username, settings.admin_password, settings.auth_secret)
    settings.admin_username = extra.get("username", "admin")
    settings.admin_password = password
    client = TestClient(app)
    client._restore = lambda: (  # type: ignore[attr-defined]
        setattr(settings, "admin_username", original[0]),
        setattr(settings, "admin_password", original[1]),
        setattr(settings, "auth_secret", original[2]),
    )
    return client


def test_login_disabled_when_no_password():
    client = _client(password="")
    try:
        assert client.get("/api/auth/me").json() == {"auth_required": False, "authenticated": True, "username": "admin"}
        assert client.get("/api/config").status_code == 200
    finally:
        client._restore()


def test_api_requires_login_and_cookie_grants_access():
    client = _client()
    try:
        assert client.get("/api/config").status_code == 401
        assert client.get("/api/health").status_code == 200  # health stays public
        me = client.get("/api/auth/me").json()
        assert me["auth_required"] is True and me["authenticated"] is False

        bad = client.post("/api/auth/login", json={"username": "admin", "password": "nope"})
        assert bad.status_code == 401

        ok = client.post("/api/auth/login", json={"username": "ADMIN", "password": "s3cret-pass"})
        assert ok.status_code == 200 and ok.json()["authenticated"] is True
        set_cookie = ok.headers["set-cookie"].lower()
        assert "httponly" in set_cookie and "samesite=lax" in set_cookie and "max-age=31536000" in set_cookie

        assert client.get("/api/config").status_code == 200
        assert client.get("/api/auth/me").json()["authenticated"] is True

        client.post("/api/auth/logout")
        assert client.get("/api/config").status_code == 401
    finally:
        client._restore()


def test_websocket_needs_session():
    client = _client()
    try:
        try:
            with client.websocket_connect("/api/ws"):
                raise AssertionError("socket should have been rejected")
        except Exception as exc:  # starlette raises WebSocketDisconnect
            assert "AssertionError" not in type(exc).__name__
        client.post("/api/auth/login", json={"username": "admin", "password": "s3cret-pass"})
        with client.websocket_connect("/api/ws") as socket:
            assert socket.receive_json()["type"] == "state"
    finally:
        client._restore()


def test_tampered_or_expired_tokens_rejected():
    settings = Settings(admin_password="pw", auth_session_days=1)
    auth = AuthService(settings)
    token = auth.issue_token()
    assert auth.verify_token(token)
    body, _, sig = token.rpartition(".")
    assert not auth.verify_token(body + "." + "0" * len(sig))
    assert not auth.verify_token(body + "x." + sig)
    assert not auth.verify_token("garbage")
    # changing the password invalidates old sessions
    settings.admin_password = "other"
    assert not auth.verify_token(token)
    # expiry
    settings.admin_password = "pw"
    settings.auth_session_days = 1
    expired = AuthService(settings)
    real = time.time
    try:
        time.time = lambda: real() + 2 * 86400  # type: ignore[assignment]
        assert not expired.verify_token(token)
    finally:
        time.time = real  # type: ignore[assignment]


def test_login_lockout_after_repeated_failures():
    client = _client()
    try:
        for _ in range(MAX_FAILURES - 1):
            assert client.post("/api/auth/login", json={"username": "admin", "password": "x"}).status_code == 401
        locked = client.post("/api/auth/login", json={"username": "admin", "password": "x"})
        assert locked.status_code == 429 and "Retry-After" in locked.headers
        # even the right password is refused while locked
        assert client.post("/api/auth/login", json={"username": "admin", "password": "s3cret-pass"}).status_code == 429
    finally:
        client._restore()


def test_api_token_header_still_works_alongside_login():
    client = _client()
    settings = client.app.state.settings
    settings.api_token = "tok"
    try:
        assert client.get("/api/config").status_code == 401
        assert client.get("/api/config", headers={"X-API-Key": "tok"}).status_code == 200
        assert COOKIE_NAME not in client.cookies
    finally:
        settings.api_token = ""
        client._restore()
