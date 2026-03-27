"""Regression tests for doctor.py diagnostic checks.

Covers three fixes made on branch fix/doctor-false-diagnostics:

Fix 1: _check_git_configured() must pass --global to git config so that
        gitconfig includes (e.g. ~/.config/git/config) are honoured.

Fix 2: _check_memory_dir() is the existing check; the companion fix in
        routes.py step_modules() now creates the memory directory so the
        doctor no longer false-positives right after a fresh setup.
        (The directory-creation logic is tested in test_routes_wizard.py;
        this file tests the check itself.)

Fix 3: _check_voice_configured() must accept AZURE_OPENAI_API_KEY in
        addition to OPENAI_API_KEY, in both the live environment and
        in keys.env.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock, patch

import pytest


# ---------------------------------------------------------------------------
# Fix 1: git config --global flag
# ---------------------------------------------------------------------------


class TestCheckGitConfigured:
    """Regression tests: git config check must use --global."""

    def test_uses_global_flag_for_user_name(self) -> None:
        """--global must be passed in the user.name subprocess call."""
        from amplifier_distro.doctor import _check_git_configured

        with patch("amplifier_distro.doctor.subprocess.run") as mock_run:
            mock_run.return_value.stdout = "Test User"
            mock_run.return_value.returncode = 0

            _check_git_configured()

            calls = mock_run.call_args_list
            assert len(calls) == 2, "expected exactly two subprocess.run calls"
            name_cmd = calls[0].args[0]  # ["git", "config", "--global", "user.name"]
            assert "--global" in name_cmd, (
                f"--global missing from user.name call: {name_cmd}"
            )

    def test_uses_global_flag_for_user_email(self) -> None:
        """--global must be passed in the user.email subprocess call."""
        from amplifier_distro.doctor import _check_git_configured

        with patch("amplifier_distro.doctor.subprocess.run") as mock_run:
            mock_run.return_value.stdout = "test@example.com"
            mock_run.return_value.returncode = 0

            _check_git_configured()

            calls = mock_run.call_args_list
            email_cmd = calls[1].args[0]  # ["git", "config", "--global", "user.email"]
            assert "--global" in email_cmd, (
                f"--global missing from user.email call: {email_cmd}"
            )

    def test_ok_when_both_name_and_email_set(self) -> None:
        """Returns ok when git user.name and user.email are both configured."""
        from amplifier_distro.doctor import CheckStatus, _check_git_configured

        def _side_effect(cmd, **kwargs):
            result = Mock()
            result.returncode = 0
            result.stdout = "Jane Doe" if "user.name" in cmd else "jane@example.com"
            return result

        with patch("amplifier_distro.doctor.subprocess.run", side_effect=_side_effect):
            check = _check_git_configured()

        assert check.status == CheckStatus.ok
        assert "Jane Doe" in check.message

    def test_warning_when_user_name_missing(self) -> None:
        """Returns warning and names the missing key when user.name is empty."""
        from amplifier_distro.doctor import CheckStatus, _check_git_configured

        def _side_effect(cmd, **kwargs):
            result = Mock()
            result.returncode = 0
            # Return empty string for user.name, valid value for user.email
            result.stdout = "" if "user.name" in cmd else "jane@example.com"
            return result

        with patch("amplifier_distro.doctor.subprocess.run", side_effect=_side_effect):
            check = _check_git_configured()

        assert check.status == CheckStatus.warning
        assert "user.name" in check.message

    def test_warning_when_user_email_missing(self) -> None:
        """Returns warning and names the missing key when user.email is empty."""
        from amplifier_distro.doctor import CheckStatus, _check_git_configured

        def _side_effect(cmd, **kwargs):
            result = Mock()
            result.returncode = 0
            result.stdout = "Jane Doe" if "user.name" in cmd else ""
            return result

        with patch("amplifier_distro.doctor.subprocess.run", side_effect=_side_effect):
            check = _check_git_configured()

        assert check.status == CheckStatus.warning
        assert "user.email" in check.message

    def test_error_when_git_not_installed(self) -> None:
        """Returns error when git is not on PATH."""
        from amplifier_distro.doctor import CheckStatus, _check_git_configured

        with patch(
            "amplifier_distro.doctor.subprocess.run",
            side_effect=FileNotFoundError,
        ):
            check = _check_git_configured()

        assert check.status == CheckStatus.error


# ---------------------------------------------------------------------------
# Fix 3: voice check accepts Azure OpenAI key
# ---------------------------------------------------------------------------


class TestCheckVoiceConfigured:
    """Regression tests: voice check must accept AZURE_OPENAI_API_KEY."""

    def test_ok_with_openai_key_in_environment(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """OPENAI_API_KEY in env → ok."""
        from amplifier_distro.doctor import CheckStatus, _check_voice_configured

        monkeypatch.delenv("AZURE_OPENAI_API_KEY", raising=False)
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test123")

        check = _check_voice_configured(tmp_path)

        assert check.status == CheckStatus.ok
        assert "OPENAI_API_KEY" in check.message

    def test_ok_with_azure_key_in_environment(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """AZURE_OPENAI_API_KEY in env → ok (the regression fix)."""
        from amplifier_distro.doctor import CheckStatus, _check_voice_configured

        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.setenv("AZURE_OPENAI_API_KEY", "azure-test-key-123")

        check = _check_voice_configured(tmp_path)

        assert check.status == CheckStatus.ok
        assert "AZURE_OPENAI_API_KEY" in check.message

    def test_ok_with_openai_key_in_keys_env(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """OPENAI_API_KEY in keys.env → ok."""
        from amplifier_distro.doctor import CheckStatus, _check_voice_configured

        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("AZURE_OPENAI_API_KEY", raising=False)
        (tmp_path / "keys.env").write_text('OPENAI_API_KEY="sk-from-keys-env"\n')

        check = _check_voice_configured(tmp_path)

        assert check.status == CheckStatus.ok
        assert "OPENAI_API_KEY" in check.message

    def test_ok_with_azure_key_in_keys_env(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """AZURE_OPENAI_API_KEY in keys.env → ok (the regression fix, file path)."""
        from amplifier_distro.doctor import CheckStatus, _check_voice_configured

        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("AZURE_OPENAI_API_KEY", raising=False)
        (tmp_path / "keys.env").write_text('AZURE_OPENAI_API_KEY="azure-from-file"\n')

        check = _check_voice_configured(tmp_path)

        assert check.status == CheckStatus.ok
        assert "AZURE_OPENAI_API_KEY" in check.message

    def test_openai_env_takes_precedence_over_keys_env(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Environment variable is checked before keys.env."""
        from amplifier_distro.doctor import CheckStatus, _check_voice_configured

        monkeypatch.setenv("OPENAI_API_KEY", "sk-env-wins")
        (tmp_path / "keys.env").write_text('OPENAI_API_KEY="sk-file-loses"\n')

        check = _check_voice_configured(tmp_path)

        assert check.status == CheckStatus.ok
        assert "environment" in check.message

    def test_warning_when_no_key_present(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Warning (not ok) when neither key is found anywhere."""
        from amplifier_distro.doctor import CheckStatus, _check_voice_configured

        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("AZURE_OPENAI_API_KEY", raising=False)
        # No keys.env file in tmp_path

        check = _check_voice_configured(tmp_path)

        assert check.status == CheckStatus.warning
        # Message should mention both providers so the user knows what to set
        assert "Azure" in check.message or "OpenAI" in check.message

    def test_warning_message_mentions_azure(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Warning message must mention Azure so users know it is an option."""
        from amplifier_distro.doctor import CheckStatus, _check_voice_configured

        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("AZURE_OPENAI_API_KEY", raising=False)

        check = _check_voice_configured(tmp_path)

        assert check.status == CheckStatus.warning
        assert "Azure" in check.message, (
            "Warning must mention Azure so users know AZURE_OPENAI_API_KEY is accepted"
        )


# ---------------------------------------------------------------------------
# Fix 2 (doctor side): memory directory check
# ---------------------------------------------------------------------------


class TestCheckMemoryDir:
    """Tests for the memory-directory diagnostic check.

    The companion provisioning fix (routes.py step_modules) is covered in
    test_routes_wizard.py::test_step_modules_creates_memory_dir_for_dev_memory.
    """

    def test_ok_when_memory_dir_exists(self, tmp_path: Path) -> None:
        """Returns ok when the memory directory is present and writable."""
        from amplifier_distro.doctor import CheckStatus, _check_memory_dir

        (tmp_path / "memory").mkdir()

        check = _check_memory_dir(tmp_path)

        assert check.status == CheckStatus.ok

    def test_warning_when_memory_dir_missing(self, tmp_path: Path) -> None:
        """Returns warning with a fix available when memory dir does not exist."""
        from amplifier_distro.doctor import CheckStatus, _check_memory_dir

        # tmp_path exists but has no "memory" subdirectory
        check = _check_memory_dir(tmp_path)

        assert check.status == CheckStatus.warning
        assert check.fix_available is True

    def test_ok_message_contains_path(self, tmp_path: Path) -> None:
        """Ok message contains the resolved directory path."""
        from amplifier_distro.doctor import CheckStatus, _check_memory_dir

        memory_dir = tmp_path / "memory"
        memory_dir.mkdir()

        check = _check_memory_dir(tmp_path)

        assert check.status == CheckStatus.ok
        assert "memory" in check.message
