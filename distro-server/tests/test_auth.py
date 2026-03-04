"""Tests for PAM authentication module."""

import logging
from unittest.mock import MagicMock, patch

from amplifier_distro.server.auth import authenticate_pam, is_auth_applicable


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
