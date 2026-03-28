"""Tests for CLI server startup logic."""

from __future__ import annotations

import os  # noqa: F401 — available for env-var assertions in test cases
import socket
from unittest.mock import patch

from click.testing import CliRunner

from amplifier_distro.cli import main


class TestPortConflictDetection:
    def test_exits_with_error_when_port_in_use(self) -> None:
        """When the default port is occupied, amp-distro should fail with a clear error."""
        # Hold a port
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(("127.0.0.1", 0))
            _, held_port = s.getsockname()
            s.listen(1)

            runner = CliRunner()
            result = runner.invoke(main, ["--port", str(held_port)])
            assert result.exit_code != 0
            assert (
                "already in use" in result.output.lower()
                or "already in use" in (result.stderr_bytes or b"").decode().lower()
            )

    def test_port_available_proceeds_to_amplifierd(self) -> None:
        """When the port is available, startup should proceed (we mock amplifierd)."""
        with patch("amplifier_distro.cli.click.get_current_context") as mock_ctx:
            # Mock the context and invoke to avoid actually starting amplifierd
            mock_ctx.return_value.invoke.return_value = None

            runner = CliRunner()
            # Use a random free port
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(("127.0.0.1", 0))
                _, free_port = s.getsockname()

            # This will fail when trying to import amplifierd.cli, which is expected
            # in a test environment. The important thing is the port check passes.
            result = runner.invoke(main, ["--port", str(free_port)])
            # If we get past the port check, we'll hit an import error for amplifierd
            # which is fine — we're testing the port validation, not the full startup
            if result.exit_code != 0:
                assert "already in use" not in (result.output or "").lower()


class TestSmartDefaults:
    """Tests for smart host-based defaults in the bare amp-distro command.

    The bare command should choose sane defaults based on the --host value:
    - localhost / 127.0.0.1  → TLS off (local development, no auth required)
    - 0.0.0.0 (public)       → TLS auto, auth enabled (remote-access safe defaults)
    Explicit flags always override the smart defaults.
    """

    def test_bare_command_defaults_to_localhost_tls_off(self) -> None:
        """Bare command with no flags defaults to 127.0.0.1 with TLS off."""
        with patch("amplifier_distro.cli._start_server") as mock_start:
            runner = CliRunner()
            result = runner.invoke(main, [])
            assert result.exit_code == 0, result.output
            mock_start.assert_called_once()
            kwargs = mock_start.call_args.kwargs
            assert kwargs["host"] == "127.0.0.1"
            assert kwargs["tls_mode"] == "off"

    def test_host_0_0_0_0_defaults_tls_auto_and_auth_enabled(self) -> None:
        """--host 0.0.0.0 defaults TLS to 'auto' and enables auth by default."""
        with patch("amplifier_distro.cli._start_server") as mock_start:
            runner = CliRunner()
            result = runner.invoke(main, ["--host", "0.0.0.0"])
            assert result.exit_code == 0, result.output
            mock_start.assert_called_once()
            kwargs = mock_start.call_args.kwargs
            assert kwargs["tls_mode"] == "auto"
            assert kwargs.get("auth_by_default") is True

    def test_explicit_tls_overrides_smart_default(self) -> None:
        """Explicit --tls flag overrides the smart default for 0.0.0.0."""
        with patch("amplifier_distro.cli._start_server") as mock_start:
            runner = CliRunner()
            result = runner.invoke(main, ["--host", "0.0.0.0", "--tls", "off"])
            assert result.exit_code == 0, result.output
            mock_start.assert_called_once()
            kwargs = mock_start.call_args.kwargs
            assert kwargs["tls_mode"] == "off"

    def test_localhost_keeps_tls_off(self) -> None:
        """--host localhost keeps TLS off (same safe default as 127.0.0.1)."""
        with patch("amplifier_distro.cli._start_server") as mock_start:
            runner = CliRunner()
            result = runner.invoke(main, ["--host", "localhost"])
            assert result.exit_code == 0, result.output
            mock_start.assert_called_once()
            kwargs = mock_start.call_args.kwargs
            assert kwargs["tls_mode"] == "off"

    def test_no_auth_overrides_smart_default(self) -> None:
        """--no-auth overrides the auth smart default for 0.0.0.0."""
        with patch("amplifier_distro.cli._start_server") as mock_start:
            runner = CliRunner()
            result = runner.invoke(main, ["--host", "0.0.0.0", "--no-auth"])
            assert result.exit_code == 0, result.output
            mock_start.assert_called_once()
            kwargs = mock_start.call_args.kwargs
            assert kwargs["no_auth"] is True
            assert kwargs.get("auth_by_default") is not True


class TestServeSubcommandRemoved:
    """Tests verifying that 'amp-distro serve' subcommand no longer exists.

    The 'serve' subcommand should be removed in favour of smart defaults on
    the bare command (--host 0.0.0.0 triggers remote-mode defaults).
    """

    def test_serve_subcommand_fails_with_no_such_command(self) -> None:
        """'amp-distro serve' should fail with 'No such command'."""
        runner = CliRunner()
        result = runner.invoke(main, ["serve"])
        assert result.exit_code != 0
        assert "no such command" in result.output.lower()


class TestNetworkWithoutTlsWarning:
    """Tests for the warning emitted when running on a network interface without TLS.

    When the user explicitly passes --tls off together with a non-localhost
    host, amp-distro should emit a clearly visible warning so the operator
    knows the service is exposed without encryption.
    """

    def test_warning_emitted_on_network_without_tls(self) -> None:
        """--host 0.0.0.0 --tls off should emit a 'without TLS' warning to stderr."""
        with patch("amplifier_distro.cli._start_server", side_effect=SystemExit(0)):
            runner = CliRunner()
            result = runner.invoke(main, ["--host", "0.0.0.0", "--tls", "off"])
            combined = result.output + (result.stderr_bytes or b"").decode()
            assert "without TLS" in combined

    def test_no_warning_on_localhost(self) -> None:
        """Default localhost invocation should NOT emit a 'without TLS' warning."""
        with patch("amplifier_distro.cli._start_server", side_effect=SystemExit(0)):
            runner = CliRunner()
            result = runner.invoke(main, [])
            combined = result.output + (result.stderr_bytes or b"").decode()
            assert "without TLS" not in combined

    def test_no_warning_on_network_with_tls(self) -> None:
        """--host 0.0.0.0 with TLS auto (default) should NOT emit a 'without TLS' warning."""
        with patch("amplifier_distro.cli._start_server", side_effect=SystemExit(0)):
            runner = CliRunner()
            result = runner.invoke(main, ["--host", "0.0.0.0"])
            combined = result.output + (result.stderr_bytes or b"").decode()
            assert "without TLS" not in combined
