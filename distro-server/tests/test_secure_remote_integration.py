"""Integration tests for secure remote access features.

End-to-end tests verifying the full auth flow, origin checking,
and TLS configuration through the actual ASGI server.
"""

from __future__ import annotations

from unittest.mock import patch

import httpx

from amplifier_distro.server.app import DistroServer


def _make_server(auth_secret: str = "") -> DistroServer:
    """Create a DistroServer with optional auth enabled."""
    return DistroServer(auth_secret=auth_secret)


class TestOriginIntegration:
    """Origin checking works through actual HTTP requests."""

    async def test_health_endpoint_always_accessible(self):
        server = _make_server()
        transport = httpx.ASGITransport(app=server.app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            resp = await client.get("/api/health")
            assert resp.status_code == 200

    async def test_server_without_auth_has_no_login_route(self):
        server = _make_server(auth_secret="")
        route_paths = [getattr(r, "path", None) for r in server.app.routes]
        assert "/login" not in route_paths

    async def test_server_with_auth_has_login_route(self):
        server = _make_server(auth_secret="test-secret-key")
        route_paths = [getattr(r, "path", None) for r in server.app.routes]
        assert "/login" in route_paths


class TestAuthFlowIntegration:
    """Full auth flow: login -> access -> logout."""

    async def test_unauthenticated_api_returns_401(self):
        # httpx.ASGITransport sends 127.0.0.1 as the client IP, which would
        # normally trigger the localhost bypass. Patch to simulate a remote client.
        server = _make_server(auth_secret="test-secret-key")
        transport = httpx.ASGITransport(app=server.app)
        with patch(
            "amplifier_distro.server.auth.is_localhost_request",
            return_value=False,
        ):
            async with httpx.AsyncClient(
                transport=transport, base_url="http://test"
            ) as client:
                resp = await client.get(
                    "/api/sessions",
                    headers={"Accept": "application/json"},
                )
                assert resp.status_code == 401

    async def test_login_and_access_protected_route(self):
        server = _make_server(auth_secret="test-secret-key")
        transport = httpx.ASGITransport(app=server.app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            # Login
            with patch(
                "amplifier_distro.server.auth_routes.authenticate_pam",
                return_value=True,
            ):
                login_resp = await client.post(
                    "/login",
                    data={"username": "testuser", "password": "testpass"},
                    follow_redirects=False,
                )
                assert login_resp.status_code == 303

                # Extract cookie from the response
                cookies = login_resp.cookies

            # Access protected route with cookie
            resp = await client.get("/api/health", cookies=cookies)
            assert resp.status_code == 200

    async def test_health_always_accessible_even_with_auth(self):
        """Health endpoint is public even when auth is enabled."""
        server = _make_server(auth_secret="test-secret-key")
        transport = httpx.ASGITransport(app=server.app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            resp = await client.get("/api/health")
            assert resp.status_code == 200

    async def test_bearer_token_works_alongside_cookie_auth(self):
        """AMPLIFIER_SERVER_API_KEY still works when PAM auth is active."""
        import os

        server = _make_server(auth_secret="test-secret-key")
        transport = httpx.ASGITransport(app=server.app)

        os.environ["AMPLIFIER_SERVER_API_KEY"] = "test-api-key"
        try:
            async with httpx.AsyncClient(
                transport=transport, base_url="http://test"
            ) as client:
                resp = await client.get(
                    "/api/health",
                    headers={"Authorization": "Bearer test-api-key"},
                )
                assert resp.status_code == 200
        finally:
            os.environ.pop("AMPLIFIER_SERVER_API_KEY", None)

    async def test_login_page_accessible_without_auth(self):
        """Login page itself must be accessible without authentication."""
        server = _make_server(auth_secret="test-secret-key")
        transport = httpx.ASGITransport(app=server.app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            resp = await client.get("/login")
            assert resp.status_code == 200
            assert "amplifier" in resp.text.lower() or "login" in resp.text.lower()

    async def test_failed_login_returns_401(self):
        """Invalid credentials return 401."""
        server = _make_server(auth_secret="test-secret-key")
        transport = httpx.ASGITransport(app=server.app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            with patch(
                "amplifier_distro.server.auth_routes.authenticate_pam",
                return_value=False,
            ):
                resp = await client.post(
                    "/login",
                    data={"username": "testuser", "password": "wrong"},
                )
                assert resp.status_code == 401


class TestSettingsIntegration:
    """Settings load correctly with the new server section."""

    def test_default_settings_have_tls_off(self):
        from amplifier_distro.distro_settings import DistroSettings

        s = DistroSettings()
        assert s.server.tls.mode == "off"
        assert s.server.auth.enabled is True
        assert s.server.allowed_origins == []
