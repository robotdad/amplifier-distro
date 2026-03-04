"""Tests for TLS, Auth, and Server settings dataclasses.

Validates:
1. TlsSettings defaults (mode='off', certfile='', keyfile='')
2. AuthSettings defaults (enabled=True, session_timeout=2592000)
3. ServerSettings nests TlsSettings and AuthSettings correctly
4. DistroSettings has a 'server' field of type ServerSettings
5. Round-trip save/load preserves tls mode, session_timeout, allowed_origins
6. DISTRO_CERTS_DIR contains 'certs' and DISTRO_HOME
"""

from __future__ import annotations

from amplifier_distro import conventions
from amplifier_distro.distro_settings import (
    AuthSettings,
    DistroSettings,
    ServerSettings,
    TlsSettings,
    load,
    save,
)

# ---------------------------------------------------------------------------
# TlsSettings defaults
# ---------------------------------------------------------------------------


class TestTlsSettingsDefaults:
    def test_mode_defaults_to_off(self):
        tls = TlsSettings()
        assert tls.mode == "off"

    def test_certfile_defaults_to_empty(self):
        tls = TlsSettings()
        assert tls.certfile == ""

    def test_keyfile_defaults_to_empty(self):
        tls = TlsSettings()
        assert tls.keyfile == ""


# ---------------------------------------------------------------------------
# AuthSettings defaults
# ---------------------------------------------------------------------------


class TestAuthSettingsDefaults:
    def test_enabled_defaults_to_true(self):
        auth = AuthSettings()
        assert auth.enabled is True

    def test_session_timeout_defaults_to_2592000(self):
        auth = AuthSettings()
        assert auth.session_timeout == 2592000


# ---------------------------------------------------------------------------
# ServerSettings nesting
# ---------------------------------------------------------------------------


class TestServerSettingsNesting:
    def test_tls_is_tls_settings_instance(self):
        server = ServerSettings()
        assert isinstance(server.tls, TlsSettings)

    def test_auth_is_auth_settings_instance(self):
        server = ServerSettings()
        assert isinstance(server.auth, AuthSettings)

    def test_allowed_origins_defaults_to_empty_list(self):
        server = ServerSettings()
        assert server.allowed_origins == []

    def test_allowed_origins_is_list(self):
        server = ServerSettings()
        assert isinstance(server.allowed_origins, list)


# ---------------------------------------------------------------------------
# DistroSettings has server field
# ---------------------------------------------------------------------------


class TestDistroSettingsServerField:
    def test_has_server_attribute(self):
        settings = DistroSettings()
        assert hasattr(settings, "server")

    def test_server_is_server_settings_instance(self):
        settings = DistroSettings()
        assert isinstance(settings.server, ServerSettings)


# ---------------------------------------------------------------------------
# Round-trip save/load
# ---------------------------------------------------------------------------


class TestRoundTrip:
    def test_tls_mode_survives_roundtrip(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AMPLIFIER_DISTRO_HOME", str(tmp_path))
        # Force conventions to pick up the new env value
        monkeypatch.setattr(conventions, "DISTRO_HOME", str(tmp_path))

        settings = DistroSettings()
        settings.server.tls.mode = "mutual"
        save(settings)

        loaded = load()
        assert loaded.server.tls.mode == "mutual"

    def test_session_timeout_survives_roundtrip(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AMPLIFIER_DISTRO_HOME", str(tmp_path))
        monkeypatch.setattr(conventions, "DISTRO_HOME", str(tmp_path))

        settings = DistroSettings()
        settings.server.auth.session_timeout = 3600
        save(settings)

        loaded = load()
        assert loaded.server.auth.session_timeout == 3600

    def test_allowed_origins_survives_roundtrip(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AMPLIFIER_DISTRO_HOME", str(tmp_path))
        monkeypatch.setattr(conventions, "DISTRO_HOME", str(tmp_path))

        settings = DistroSettings()
        settings.server.allowed_origins = ["https://example.com", "https://other.com"]
        save(settings)

        loaded = load()
        assert loaded.server.allowed_origins == [
            "https://example.com",
            "https://other.com",
        ]

    def test_defaults_survive_roundtrip(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AMPLIFIER_DISTRO_HOME", str(tmp_path))
        monkeypatch.setattr(conventions, "DISTRO_HOME", str(tmp_path))

        settings = DistroSettings()
        save(settings)

        loaded = load()
        assert loaded.server.tls.mode == "off"
        assert loaded.server.auth.enabled is True
        assert loaded.server.auth.session_timeout == 2592000
        assert loaded.server.allowed_origins == []


# ---------------------------------------------------------------------------
# DISTRO_CERTS_DIR convention
# ---------------------------------------------------------------------------


class TestDistroCertsDir:
    def test_contains_certs(self):
        assert "certs" in conventions.DISTRO_CERTS_DIR

    def test_contains_distro_home(self):
        assert conventions.DISTRO_HOME in conventions.DISTRO_CERTS_DIR
