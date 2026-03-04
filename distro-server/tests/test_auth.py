"""Tests for PAM authentication module, session token management, and auth routes."""

import logging
from unittest.mock import MagicMock, patch

import httpx
import pytest
from fastapi import FastAPI

from amplifier_distro.server.auth import (
    DEFAULT_SESSION_TIMEOUT,
    authenticate_pam,
    create_session_token,
    get_or_create_secret,
    is_auth_applicable,
    is_localhost_request,
    verify_session_token,
)
from amplifier_distro.server.auth_routes import create_auth_router


class TestIsLocalhostRequest:
    """Tests for localhost bypass detection."""

    def test_127_0_0_1_is_localhost(self):
        assert is_localhost_request("127.0.0.1") is True

    def test_localhost_string_is_localhost(self):
        assert is_localhost_request("localhost") is True

    def test_ipv6_loopback_is_localhost(self):
        assert is_localhost_request("::1") is True

    def test_remote_ip_is_not_localhost(self):
        assert is_localhost_request("192.168.1.100") is False

    def test_none_is_not_localhost(self):
        assert is_localhost_request(None) is False


class TestAuthenticatePam:
    """Tests for authenticate_pam()."""

    @patch("amplifier_distro.server.auth._pam")
    def test_returns_true_on_success(self, mock_pam_module):
        """authenticate_pam returns True when PAM authentication succeeds."""
        mock_instance = MagicMock()
        mock_instance.authenticate.return_value = True
        mock_pam_module.pam.return_value = mock_instance

        result = authenticate_pam("alice", "correct-password")

        assert result is True
        mock_instance.authenticate.assert_called_once_with("alice", "correct-password")

    @patch("amplifier_distro.server.auth._pam")
    def test_returns_false_on_failure(self, mock_pam_module):
        """authenticate_pam returns False when PAM authentication fails."""
        mock_instance = MagicMock()
        mock_instance.authenticate.return_value = False
        mock_instance.reason = "Authentication failure"
        mock_pam_module.pam.return_value = mock_instance

        result = authenticate_pam("alice", "wrong-password")

        assert result is False

    @patch("amplifier_distro.server.auth._pam")
    def test_logs_reason_on_failure(self, mock_pam_module, caplog):
        """authenticate_pam logs pam.reason on failure (server-side only)."""
        mock_instance = MagicMock()
        mock_instance.authenticate.return_value = False
        mock_instance.reason = "Authentication failure"
        mock_pam_module.pam.return_value = mock_instance

        with caplog.at_level(logging.WARNING, logger="amplifier_distro.server.auth"):
            authenticate_pam("alice", "wrong-password")

        assert any(
            "Authentication failure" in record.message for record in caplog.records
        )

    @patch("amplifier_distro.server.auth._pam", None)
    def test_returns_false_when_pam_unavailable(self, caplog):
        """Returns False and logs warning when pam module is not installed."""
        with caplog.at_level(logging.WARNING, logger="amplifier_distro.server.auth"):
            result = authenticate_pam("alice", "password")

        assert result is False
        assert any(
            "PAM module not available" in record.message for record in caplog.records
        )


class TestIsAuthApplicable:
    """Tests for is_auth_applicable()."""

    def test_not_applicable_when_tls_off(self):
        """Auth is not applicable when TLS is inactive."""
        result = is_auth_applicable(
            tls_active=False, platform="linux", auth_enabled=True
        )

        assert result is False

    def test_not_applicable_on_macos(self):
        """Auth is not applicable on macOS."""
        result = is_auth_applicable(
            tls_active=True, platform="darwin", auth_enabled=True
        )

        assert result is False

    def test_applicable_on_linux_with_tls(self):
        """Auth is applicable on Linux with TLS active and auth enabled."""
        result = is_auth_applicable(
            tls_active=True, platform="linux", auth_enabled=True
        )

        assert result is True

    def test_not_applicable_when_disabled(self):
        """Auth is not applicable when auth_enabled is False."""
        result = is_auth_applicable(
            tls_active=True, platform="linux", auth_enabled=False
        )

        assert result is False

    def test_not_applicable_on_windows(self):
        """Auth is not applicable on Windows."""
        result = is_auth_applicable(
            tls_active=True, platform="win32", auth_enabled=True
        )

        assert result is False

    def test_not_applicable_when_platform_none(self):
        """Auth is not applicable when platform is None (unknown)."""
        result = is_auth_applicable(tls_active=True, platform=None, auth_enabled=True)

        assert result is False

    def test_platform_defaults_to_none(self):
        """platform defaults to None, making auth not applicable."""
        result = is_auth_applicable(tls_active=True)

        assert result is False

    def test_auth_enabled_defaults_to_true(self):
        """auth_enabled defaults to True, so Linux + TLS is sufficient."""
        result = is_auth_applicable(tls_active=True, platform="linux")

        assert result is True


class TestSessionTokens:
    """Tests for create_session_token() and verify_session_token()."""

    def test_create_verify_round_trip(self):
        """create + verify round-trip returns the original username."""
        secret = "test-secret-key"  # noqa: S105
        token = create_session_token("alice", secret)
        result = verify_session_token(token, secret)

        assert result == "alice"

    def test_invalid_token_returns_none(self):
        """verify_session_token returns None for a garbage token."""
        result = verify_session_token("not-a-valid-token", "some-secret")

        assert result is None

    def test_wrong_secret_returns_none(self):
        """verify_session_token returns None when secret doesn't match."""
        token = create_session_token("alice", "secret-one")
        result = verify_session_token(token, "secret-two")

        assert result is None

    def test_expired_token_returns_none(self):
        """verify_session_token returns None when token exceeds max_age."""
        import time as _time

        real_time = _time.time

        # Create token at current time, then advance clock by 2s for verify
        token = create_session_token("alice", "test-key")
        with patch("itsdangerous.timed.time.time", side_effect=lambda: real_time() + 2):
            result = verify_session_token(token, "test-key", max_age=0)

        assert result is None


class TestGetOrCreateSecret:
    """Tests for get_or_create_secret()."""

    def test_creates_secret_file(self, tmp_path):
        """Creates a session-secret.key file with a long-enough secret."""
        secret = get_or_create_secret(tmp_path)

        secret_file = tmp_path / "session-secret.key"
        assert len(secret) > 16
        assert secret_file.exists()
        assert secret_file.stat().st_mode & 0o777 == 0o600

    def test_reuses_existing_secret(self, tmp_path):
        """Returns the same secret on a second call (reads from file)."""
        first = get_or_create_secret(tmp_path)
        second = get_or_create_secret(tmp_path)

        assert first == second


# ---------------------------------------------------------------------------
# Auth routes tests (httpx.AsyncClient + ASGITransport pattern)
# ---------------------------------------------------------------------------

TEST_SECRET = "test-secret-for-auth-routes"  # noqa: S105


@pytest.fixture
def auth_app():
    """Create a minimal FastAPI app with auth routes for testing."""
    app = FastAPI()
    router = create_auth_router(
        secret=TEST_SECRET, session_timeout=DEFAULT_SESSION_TIMEOUT
    )
    app.include_router(router)
    return app


@pytest.fixture
async def auth_client(auth_app):
    """Async httpx client wired to the auth FastAPI app via ASGITransport."""
    transport = httpx.ASGITransport(app=auth_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


class TestLoginRoute:
    """Tests for GET /login and POST /login."""

    async def test_get_login_serves_html(self, auth_client):
        """GET /login returns an HTML login page with a username field."""
        resp = await auth_client.get("/login")

        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]
        assert "username" in resp.text

    @patch("amplifier_distro.server.auth_routes.authenticate_pam", return_value=True)
    async def test_successful_login_sets_cookie_and_redirects(
        self, mock_pam, auth_client
    ):
        """POST /login sets amplifier_session cookie and returns 303."""
        resp = await auth_client.post(
            "/login",
            data={"username": "alice", "password": "correct"},
            follow_redirects=False,
        )

        assert resp.status_code == 303
        assert "amplifier_session" in resp.cookies
        mock_pam.assert_called_once_with("alice", "correct")

    @patch("amplifier_distro.server.auth_routes.authenticate_pam", return_value=False)
    async def test_failed_login_returns_401(self, mock_pam, auth_client):
        """POST /login with bad credentials returns 401 JSON."""
        resp = await auth_client.post(
            "/login",
            data={"username": "alice", "password": "wrong"},
        )

        assert resp.status_code == 401
        body = resp.json()
        assert body["error"] == "Authentication failed"


class TestAuthMeRoute:
    """Tests for GET /auth/me."""

    @patch("amplifier_distro.server.auth_routes.authenticate_pam", return_value=True)
    async def test_returns_username_with_valid_cookie(self, mock_pam, auth_client):
        """GET /auth/me returns username JSON with valid cookie."""
        # First login to get a cookie
        login_resp = await auth_client.post(
            "/login",
            data={"username": "alice", "password": "correct"},
            follow_redirects=False,
        )
        cookie_value = login_resp.cookies["amplifier_session"]

        # Use cookie to access /auth/me
        auth_client.cookies.set("amplifier_session", cookie_value)
        resp = await auth_client.get("/auth/me")

        assert resp.status_code == 200
        body = resp.json()
        assert body["username"] == "alice"

    async def test_returns_401_without_cookie(self, auth_client):
        """GET /auth/me returns 401 when no session cookie is present."""
        resp = await auth_client.get("/auth/me")

        assert resp.status_code == 401

    async def test_returns_401_with_invalid_cookie(self, auth_client):
        """GET /auth/me returns 401 when session cookie is tampered or expired."""
        auth_client.cookies.set("amplifier_session", "tampered-garbage-value")
        resp = await auth_client.get("/auth/me")

        assert resp.status_code == 401
        body = resp.json()
        assert body["error"] == "Invalid or expired session"


class TestLogoutRoute:
    """Tests for POST /logout."""

    @patch("amplifier_distro.server.auth_routes.authenticate_pam", return_value=True)
    async def test_logout_clears_cookie(self, mock_pam, auth_client):
        """POST /logout clears the amplifier_session cookie and redirects to /login."""
        # Login first
        login_resp = await auth_client.post(
            "/login",
            data={"username": "alice", "password": "correct"},
            follow_redirects=False,
        )
        cookie_value = login_resp.cookies["amplifier_session"]

        # Now logout
        auth_client.cookies.set("amplifier_session", cookie_value)
        resp = await auth_client.post(
            "/logout",
            follow_redirects=False,
        )

        assert resp.status_code == 303
        # The cookie should be cleared via max-age=0 or an expired date
        set_cookie = resp.headers.get("set-cookie", "")
        assert "amplifier_session" in set_cookie
        cookie_lower = set_cookie.lower()
        assert "max-age=0" in cookie_lower or "01 jan 1970" in cookie_lower


class TestAuthWiringConditional:
    """Auth is only wired when TLS is active on Linux."""

    def test_no_auth_middleware_when_no_secret(self):
        """Default server (no auth_secret) has no auth middleware."""
        from amplifier_distro.server.app import DistroServer

        server = DistroServer()
        middleware_types = [str(m) for m in server.app.user_middleware]
        assert "AuthMiddleware" not in str(middleware_types)

    def test_login_route_exists_when_auth_enabled(self):
        from amplifier_distro.server.app import DistroServer

        server = DistroServer(auth_secret="test-secret")
        route_paths = [getattr(r, "path", None) for r in server.app.routes]
        assert "/login" in route_paths

    def test_no_login_route_when_no_secret(self):
        from amplifier_distro.server.app import DistroServer

        server = DistroServer(auth_secret="")
        route_paths = [getattr(r, "path", None) for r in server.app.routes]
        assert "/login" not in route_paths
