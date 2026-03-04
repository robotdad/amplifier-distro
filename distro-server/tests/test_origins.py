"""Tests for amplifier_distro.server.origins module."""

from __future__ import annotations

from unittest.mock import patch

from amplifier_distro.server.origins import build_allowed_origins, is_origin_allowed

# ---------------------------------------------------------------------------
# build_allowed_origins
# ---------------------------------------------------------------------------


class TestBuildAllowedOrigins:
    """Tests for build_allowed_origins()."""

    def test_always_includes_localhost(self):
        with patch("amplifier_distro.server.origins.get_dns_name", return_value=None):
            origins = build_allowed_origins()
            assert "localhost" in origins

    def test_always_includes_127_0_0_1(self):
        with patch("amplifier_distro.server.origins.get_dns_name", return_value=None):
            origins = build_allowed_origins()
            assert "127.0.0.1" in origins

    def test_includes_tailscale_dns_when_available(self):
        with patch(
            "amplifier_distro.server.origins.get_dns_name",
            return_value="box.tail1234.ts.net",
        ):
            origins = build_allowed_origins()
            assert "box.tail1234.ts.net" in origins

    def test_excludes_tailscale_dns_when_unavailable(self):
        with patch("amplifier_distro.server.origins.get_dns_name", return_value=None):
            origins = build_allowed_origins()
            # Should only have localhost, 127.0.0.1, and hostname
            for entry in origins:
                assert "ts.net" not in entry

    def test_includes_system_hostname(self):
        with (
            patch("amplifier_distro.server.origins.get_dns_name", return_value=None),
            patch(
                "amplifier_distro.server.origins.socket.gethostname",
                return_value="my-workstation",
            ),
        ):
            origins = build_allowed_origins()
            assert "my-workstation" in origins

    def test_includes_extras(self):
        with patch("amplifier_distro.server.origins.get_dns_name", return_value=None):
            origins = build_allowed_origins(extra=["https://my-proxy.example.com"])
            assert "https://my-proxy.example.com" in origins

    def test_deduplicates(self):
        with patch("amplifier_distro.server.origins.get_dns_name", return_value=None):
            origins = build_allowed_origins(
                extra=["localhost", "127.0.0.1", "localhost"]
            )
            assert len(origins) == len(set(origins))

    def test_extra_none_treated_as_empty(self):
        with patch("amplifier_distro.server.origins.get_dns_name", return_value=None):
            origins_none = build_allowed_origins(extra=None)
            origins_default = build_allowed_origins()
            assert origins_none == origins_default


# ---------------------------------------------------------------------------
# is_origin_allowed
# ---------------------------------------------------------------------------


class TestIsOriginAllowed:
    """Tests for is_origin_allowed()."""

    def test_none_origin_is_allowed(self):
        allowed = {"localhost", "127.0.0.1"}
        assert is_origin_allowed(None, allowed) is True

    def test_localhost_origin_allowed(self):
        allowed = {"localhost", "127.0.0.1"}
        assert is_origin_allowed("http://localhost:8400", allowed) is True

    def test_127_0_0_1_origin_allowed(self):
        allowed = {"localhost", "127.0.0.1"}
        assert is_origin_allowed("http://127.0.0.1:8400", allowed) is True

    def test_tailscale_origin_allowed(self):
        allowed = {"localhost", "127.0.0.1", "box.tail1234.ts.net"}
        assert is_origin_allowed("https://box.tail1234.ts.net", allowed) is True

    def test_evil_origin_rejected(self):
        allowed = {"localhost", "127.0.0.1"}
        assert is_origin_allowed("https://evil.com", allowed) is False

    def test_custom_proxy_origin_allowed(self):
        allowed = {"localhost", "127.0.0.1", "https://my-proxy.example.com"}
        assert is_origin_allowed("https://my-proxy.example.com/path", allowed) is True

    def test_empty_origin_rejected(self):
        allowed = {"localhost", "127.0.0.1"}
        assert is_origin_allowed("", allowed) is False
