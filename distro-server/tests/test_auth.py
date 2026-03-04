"""Tests for PAM authentication module and session token management."""

import logging
from unittest.mock import MagicMock, patch

from amplifier_distro.server.auth import (
    authenticate_pam,
    create_session_token,
    get_or_create_secret,
    is_auth_applicable,
    verify_session_token,
)


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
