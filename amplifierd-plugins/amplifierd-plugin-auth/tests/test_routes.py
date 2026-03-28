"""Tests for auth plugin route cookie configurability.

Verifies that cookie attributes (secure, samesite) are driven by
``app.state.settings`` rather than hardcoded values.

Run:
    cd amplifierd-plugins/amplifierd-plugin-auth
    uv run pytest tests/test_routes.py -v

Expected result (before implementation): FAIL
  create_auth_router currently uses hardcoded secure=True and samesite="strict"
  and does not read from request.app.state.settings.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import auth_plugin.routes
from auth_plugin.routes import COOKIE_NAME, create_auth_router


def _make_auth_app(
    cookie_secure: str | bool = "auto",
    cookie_samesite: str = "lax",
    tls_mode: str = "tls_auto",
) -> FastAPI:
    """Create a minimal FastAPI app with auth router and configured settings.

    Attaches a ``SimpleNamespace`` settings object to ``app.state.settings``
    so that the router can read cookie configuration at request time.
    """
    app = FastAPI()
    app.state.settings = SimpleNamespace(
        cookie_secure=cookie_secure,
        cookie_samesite=cookie_samesite,
        tls_mode=tls_mode,
    )
    router = create_auth_router("test-secret")
    app.include_router(router)
    return app


class TestCookieSecureAttribute:
    """Cookie 'secure' flag reflects settings.cookie_secure and settings.tls_mode."""

    def test_auto_with_tls_auto_sets_secure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """auto + tls_auto → secure flag present in set-cookie header."""
        monkeypatch.setattr(auth_plugin.routes, "authenticate_pam", lambda u, p: True)
        app = _make_auth_app(cookie_secure="auto", tls_mode="tls_auto")
        client = TestClient(app, follow_redirects=False)
        resp = client.post("/login", data={"username": "user", "password": "pass"})
        cookie_header = resp.headers.get("set-cookie", "")
        assert "secure" in cookie_header.lower(), (
            f"Expected 'secure' in set-cookie header, got: {cookie_header!r}"
        )

    def test_auto_with_tls_off_omits_secure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """auto + tls_off → secure flag absent from set-cookie header."""
        monkeypatch.setattr(auth_plugin.routes, "authenticate_pam", lambda u, p: True)
        app = _make_auth_app(cookie_secure="auto", tls_mode="tls_off")
        client = TestClient(app, follow_redirects=False)
        resp = client.post("/login", data={"username": "user", "password": "pass"})
        cookie_header = resp.headers.get("set-cookie", "")
        assert "secure" not in cookie_header.lower(), (
            f"Expected no 'secure' in set-cookie header, got: {cookie_header!r}"
        )

    def test_explicit_true_overrides_auto(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Explicit True → secure present even when tls_mode=tls_off."""
        monkeypatch.setattr(auth_plugin.routes, "authenticate_pam", lambda u, p: True)
        app = _make_auth_app(cookie_secure=True, tls_mode="tls_off")
        client = TestClient(app, follow_redirects=False)
        resp = client.post("/login", data={"username": "user", "password": "pass"})
        cookie_header = resp.headers.get("set-cookie", "")
        assert "secure" in cookie_header.lower(), (
            f"Expected 'secure' in set-cookie header, got: {cookie_header!r}"
        )

    def test_explicit_false_overrides_auto(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Explicit False → secure absent even when tls_mode=tls_auto."""
        monkeypatch.setattr(auth_plugin.routes, "authenticate_pam", lambda u, p: True)
        app = _make_auth_app(cookie_secure=False, tls_mode="tls_auto")
        client = TestClient(app, follow_redirects=False)
        resp = client.post("/login", data={"username": "user", "password": "pass"})
        cookie_header = resp.headers.get("set-cookie", "")
        assert "secure" not in cookie_header.lower(), (
            f"Expected no 'secure' in set-cookie header, got: {cookie_header!r}"
        )


class TestCookieSameSiteAttribute:
    """Cookie 'samesite' attribute reflects settings.cookie_samesite."""

    def test_default_samesite_is_lax(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Default cookie_samesite='lax' → samesite=lax in set-cookie header."""
        monkeypatch.setattr(auth_plugin.routes, "authenticate_pam", lambda u, p: True)
        app = _make_auth_app(cookie_samesite="lax")
        client = TestClient(app, follow_redirects=False)
        resp = client.post("/login", data={"username": "user", "password": "pass"})
        cookie_header = resp.headers.get("set-cookie", "")
        assert "samesite=lax" in cookie_header.lower(), (
            f"Expected 'samesite=lax' in set-cookie header, got: {cookie_header!r}"
        )

    def test_explicit_strict_samesite(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Explicit cookie_samesite='strict' → samesite=strict in set-cookie header."""
        monkeypatch.setattr(auth_plugin.routes, "authenticate_pam", lambda u, p: True)
        app = _make_auth_app(cookie_samesite="strict")
        client = TestClient(app, follow_redirects=False)
        resp = client.post("/login", data={"username": "user", "password": "pass"})
        cookie_header = resp.headers.get("set-cookie", "")
        assert "samesite=strict" in cookie_header.lower(), (
            f"Expected 'samesite=strict' in set-cookie header, got: {cookie_header!r}"
        )


class TestDeleteCookieAttributes:
    """Logout endpoint clears the session cookie with correct attributes."""

    def test_logout_clears_cookie(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Logout sets COOKIE_NAME with max-age=0 or expires, and path=/."""
        monkeypatch.setattr(auth_plugin.routes, "authenticate_pam", lambda u, p: True)
        app = _make_auth_app()
        client = TestClient(app, follow_redirects=False)
        resp = client.post("/logout")
        cookie_header = resp.headers.get("set-cookie", "")
        assert COOKIE_NAME in cookie_header, (
            f"Expected {COOKIE_NAME!r} in set-cookie header, got: {cookie_header!r}"
        )
        assert "path=/" in cookie_header.lower(), (
            f"Expected 'path=/' in set-cookie header, got: {cookie_header!r}"
        )
        has_max_age_zero = "max-age=0" in cookie_header.lower()
        has_expires = "expires=" in cookie_header.lower()
        assert has_max_age_zero or has_expires, (
            f"Expected max-age=0 or expires= in set-cookie header, got: {cookie_header!r}"
        )
