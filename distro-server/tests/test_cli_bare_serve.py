"""Tests for bare `amp-distro` (no subcommand) starting on localhost.

Verifies:
1. Bare invocation calls _run_foreground with host="127.0.0.1"
2. Bare invocation prints the localhost hint/tip message
3. Bare invocation exits with code 0
4. `amp-distro serve` still defaults to host="0.0.0.0" (no regression)
"""

from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from amplifier_distro.cli import main


class TestBareInvocation:
    """Bare `amp-distro` (no subcommand) must start the server on localhost."""

    def test_bare_invocation_calls_run_foreground_with_localhost(self) -> None:
        """Bare `amp-distro` must call _run_foreground with host='127.0.0.1'."""
        runner = CliRunner()
        mock_run = MagicMock()

        with patch("amplifier_distro.server.cli._run_foreground", mock_run):
            result = runner.invoke(main, [])

        assert result.exit_code == 0, (
            f"Expected exit code 0, got {result.exit_code}\nOutput:\n{result.output}"
        )
        mock_run.assert_called_once()
        call_args = mock_run.call_args
        # host must be localhost — could be positional or keyword
        host = call_args.args[0] if call_args.args else call_args.kwargs.get("host")
        assert host == "127.0.0.1", (
            f"Expected host='127.0.0.1', got host={host!r}"
        )

    def test_bare_invocation_prints_tip_message(self) -> None:
        """Bare `amp-distro` must print the specific localhost/serve tip."""
        runner = CliRunner()
        mock_run = MagicMock()

        with patch("amplifier_distro.server.cli._run_foreground", mock_run):
            result = runner.invoke(main, [])

        tip = "Tip: Use 'amp-distro serve' to enable access from other devices"
        assert tip in result.output, (
            f"Expected tip message in output. Got:\n{result.output}"
        )

    def test_bare_invocation_exits_zero(self) -> None:
        """Bare `amp-distro` must exit with code 0 and actually start the server."""
        runner = CliRunner()
        mock_run = MagicMock()

        with patch("amplifier_distro.server.cli._run_foreground", mock_run):
            result = runner.invoke(main, [])

        assert result.exit_code == 0
        # It must actually call _run_foreground, not just show help and exit
        mock_run.assert_called_once()


class TestServeCommandUnchanged:
    """`amp-distro serve` must still default to host='0.0.0.0' (no regression)."""

    def test_serve_defaults_to_all_interfaces(self) -> None:
        """`amp-distro serve` must call _run_foreground with host='0.0.0.0'."""
        runner = CliRunner()
        mock_run = MagicMock()

        with patch("amplifier_distro.server.cli._run_foreground", mock_run):
            result = runner.invoke(main, ["serve"])

        assert result.exit_code == 0, (
            f"Expected exit code 0, got {result.exit_code}\nOutput:\n{result.output}"
        )
        mock_run.assert_called_once()
        call_args = mock_run.call_args
        host = call_args.args[0] if call_args.args else call_args.kwargs.get("host")
        assert host == "0.0.0.0", (
            f"Expected host='0.0.0.0' for `serve` subcommand, got host={host!r}"
        )
