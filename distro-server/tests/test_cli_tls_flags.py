"""Tests for TLS-related CLI flags on the serve command.

Tests cover:
1. --tls auto flag is accepted
2. --tls off is the default
3. --ssl-certfile implies manual TLS mode
4. --no-auth flag is accepted
5. All four new flags appear in --help output
6. Plain 'amp-distro serve' stays HTTP (zero breaking changes)
"""

from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from amplifier_distro.cli import main


class TestTlsFlags:
    """Verify TLS CLI flags on the top-level serve command."""

    def test_help_shows_tls_flag(self) -> None:
        """'amp-distro serve --help' must list --tls."""
        runner = CliRunner()
        result = runner.invoke(main, ["serve", "--help"])
        assert result.exit_code == 0
        assert "--tls" in result.output

    def test_help_shows_ssl_certfile_flag(self) -> None:
        """'amp-distro serve --help' must list --ssl-certfile."""
        runner = CliRunner()
        result = runner.invoke(main, ["serve", "--help"])
        assert result.exit_code == 0
        assert "--ssl-certfile" in result.output

    def test_help_shows_ssl_keyfile_flag(self) -> None:
        """'amp-distro serve --help' must list --ssl-keyfile."""
        runner = CliRunner()
        result = runner.invoke(main, ["serve", "--help"])
        assert result.exit_code == 0
        assert "--ssl-keyfile" in result.output

    def test_help_shows_no_auth_flag(self) -> None:
        """'amp-distro serve --help' must list --no-auth."""
        runner = CliRunner()
        result = runner.invoke(main, ["serve", "--help"])
        assert result.exit_code == 0
        assert "--no-auth" in result.output

    def test_tls_off_is_default(self) -> None:
        """Plain 'amp-distro serve' passes tls_mode='off' (HTTP by default)."""
        runner = CliRunner()
        mock_run = MagicMock()
        with patch("amplifier_distro.server.cli._run_foreground", mock_run):
            result = runner.invoke(main, ["serve"])
        assert result.exit_code == 0
        mock_run.assert_called_once()
        _, kwargs = mock_run.call_args
        assert kwargs.get("tls_mode") == "off"

    def test_tls_auto_accepted(self) -> None:
        """'amp-distro serve --tls auto' is accepted and forwarded."""
        runner = CliRunner()
        mock_run = MagicMock()
        with patch("amplifier_distro.server.cli._run_foreground", mock_run):
            result = runner.invoke(main, ["serve", "--tls", "auto"])
        assert result.exit_code == 0
        mock_run.assert_called_once()
        _, kwargs = mock_run.call_args
        assert kwargs.get("tls_mode") == "auto"

    def test_tls_manual_accepted(self) -> None:
        """'amp-distro serve --tls manual' is accepted."""
        runner = CliRunner()
        mock_run = MagicMock()
        with patch("amplifier_distro.server.cli._run_foreground", mock_run):
            result = runner.invoke(main, ["serve", "--tls", "manual"])
        assert result.exit_code == 0
        mock_run.assert_called_once()
        _, kwargs = mock_run.call_args
        assert kwargs.get("tls_mode") == "manual"

    def test_tls_invalid_choice_rejected(self) -> None:
        """'amp-distro serve --tls bogus' must be rejected."""
        runner = CliRunner()
        result = runner.invoke(main, ["serve", "--tls", "bogus"])
        assert result.exit_code != 0

    def test_ssl_certfile_implies_manual(self) -> None:
        """--ssl-certfile without --tls implies manual TLS mode."""
        runner = CliRunner()
        mock_run = MagicMock()
        with patch("amplifier_distro.server.cli._run_foreground", mock_run):
            result = runner.invoke(main, ["serve", "--ssl-certfile", "/tmp/cert.pem"])
        assert result.exit_code == 0
        mock_run.assert_called_once()
        _, kwargs = mock_run.call_args
        assert kwargs.get("tls_mode") == "manual"
        assert kwargs.get("ssl_certfile") == "/tmp/cert.pem"

    def test_ssl_certfile_does_not_override_explicit_tls(self) -> None:
        """--ssl-certfile with --tls auto keeps tls_mode='auto'."""
        runner = CliRunner()
        mock_run = MagicMock()
        with patch("amplifier_distro.server.cli._run_foreground", mock_run):
            result = runner.invoke(
                main,
                ["serve", "--tls", "auto", "--ssl-certfile", "/tmp/cert.pem"],
            )
        assert result.exit_code == 0
        mock_run.assert_called_once()
        _, kwargs = mock_run.call_args
        # explicit --tls auto wins
        assert kwargs.get("tls_mode") == "auto"

    def test_ssl_keyfile_passed_through(self) -> None:
        """--ssl-keyfile value is forwarded to _run_foreground."""
        runner = CliRunner()
        mock_run = MagicMock()
        with patch("amplifier_distro.server.cli._run_foreground", mock_run):
            result = runner.invoke(
                main,
                [
                    "serve",
                    "--ssl-certfile",
                    "/tmp/cert.pem",
                    "--ssl-keyfile",
                    "/tmp/key.pem",
                ],
            )
        assert result.exit_code == 0
        mock_run.assert_called_once()
        _, kwargs = mock_run.call_args
        assert kwargs.get("ssl_keyfile") == "/tmp/key.pem"

    def test_no_auth_flag_accepted(self) -> None:
        """'amp-distro serve --no-auth' is accepted."""
        runner = CliRunner()
        mock_run = MagicMock()
        with patch("amplifier_distro.server.cli._run_foreground", mock_run):
            result = runner.invoke(main, ["serve", "--no-auth"])
        assert result.exit_code == 0
        mock_run.assert_called_once()

    def test_no_auth_flag_forwarded_to_run_foreground(self) -> None:
        """'amp-distro serve --no-auth' must pass no_auth=True to _run_foreground."""
        runner = CliRunner()
        mock_run = MagicMock()
        with patch("amplifier_distro.server.cli._run_foreground", mock_run):
            result = runner.invoke(main, ["serve", "--no-auth"])
        assert result.exit_code == 0
        mock_run.assert_called_once()
        _, kwargs = mock_run.call_args
        assert kwargs.get("no_auth") is True

    def test_no_auth_no_stub_warning(self) -> None:
        """--no-auth must NOT print a 'not yet implemented' stub warning."""
        runner = CliRunner()
        mock_run = MagicMock()
        with patch("amplifier_distro.server.cli._run_foreground", mock_run):
            result = runner.invoke(main, ["serve", "--no-auth"])
        assert result.exit_code == 0
        assert "not yet implemented" not in result.output

    def test_plain_serve_stays_http(self) -> None:
        """Plain 'amp-distro serve' must stay HTTP (zero breaking changes)."""
        runner = CliRunner()
        mock_run = MagicMock()
        with patch("amplifier_distro.server.cli._run_foreground", mock_run):
            result = runner.invoke(main, ["serve"])
        assert result.exit_code == 0
        mock_run.assert_called_once()
        _, kwargs = mock_run.call_args
        # tls_mode should be 'off' and no SSL files
        assert kwargs.get("tls_mode") == "off"
        assert kwargs.get("ssl_certfile") == ""
        assert kwargs.get("ssl_keyfile") == ""
