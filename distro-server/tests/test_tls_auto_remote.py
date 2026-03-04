"""Tests for auto-enabling TLS on `serve` (host=0.0.0.0) without Tailscale.

Validates:
1. serve + 0.0.0.0 + no Tailscale + default tls → TLS auto-promoted to 'auto'
2. serve + 0.0.0.0 + no Tailscale + explicit --tls off → escape hatch respected
3. serve + 0.0.0.0 + Tailscale active → Tailscale handles it, no self-signed
4. bare / localhost (127.0.0.1) + no Tailscale → never auto-enables TLS
5. serve + 0.0.0.0 + explicit --tls auto already → no double-promotion, works as before
"""

from __future__ import annotations

import contextlib
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Fixture - isolate _run_foreground from real services (mirrors test_tls_startup.py)
# ---------------------------------------------------------------------------


@pytest.fixture()
def foreground_env(tmp_path: Path):
    """Yield a namespace with mocks for every heavy dependency of _run_foreground.

    Patches are applied at their *source* modules because the function uses
    local ``from ... import ...`` statements that bind fresh local names each call.
    """
    session_path = tmp_path / "session"
    session_path.mkdir()
    (session_path / "meta.json").write_text("{}")

    mock_server = MagicMock()
    mock_server.app = MagicMock()
    mock_server.discover_apps.return_value = []

    mock_uvicorn = MagicMock()
    mock_resolve = MagicMock(return_value=None)  # default: TLS off -> None

    echo_calls: list[str] = []

    def _capture_echo(msg="", **kwargs):
        echo_calls.append(str(msg))

    stack = contextlib.ExitStack()
    with stack:
        stack.enter_context(patch("uvicorn.run", mock_uvicorn.run))
        stack.enter_context(
            patch("amplifier_distro.server.startup.setup_logging", MagicMock())
        )
        stack.enter_context(
            patch(
                "amplifier_distro.server.startup.load_env_file",
                MagicMock(return_value=[]),
            )
        )
        stack.enter_context(
            patch(
                "amplifier_distro.server.startup.export_keys",
                MagicMock(return_value=[]),
            )
        )
        stack.enter_context(
            patch("amplifier_distro.server.startup.log_startup_info", MagicMock())
        )
        stack.enter_context(
            patch("amplifier_distro.server.services.init_services", MagicMock())
        )
        stack.enter_context(
            patch(
                "amplifier_distro.server.app.create_server",
                MagicMock(return_value=mock_server),
            )
        )
        stack.enter_context(
            patch(
                "amplifier_distro.server.session_dir.create_session_dir",
                MagicMock(return_value=("sess-001", session_path)),
            )
        )
        stack.enter_context(
            patch(
                "amplifier_distro.server.session_dir.setup_session_log",
                MagicMock(return_value=str(session_path / "server.log")),
            )
        )
        # Module-level helpers in cli.py — default: no Tailscale
        stack.enter_context(
            patch(
                "amplifier_distro.server.cli._setup_tailscale",
                MagicMock(return_value=None),
            )
        )
        # resolve_cert - patched at source so the local import picks it up
        stack.enter_context(
            patch("amplifier_distro.server.tls.resolve_cert", mock_resolve)
        )
        # click.echo - capture output
        stack.enter_context(
            patch("amplifier_distro.server.cli.click.echo", side_effect=_capture_echo)
        )
        stack.enter_context(
            patch(
                "amplifier_distro.server.cli.click.style",
                side_effect=lambda msg, **kw: msg,
            )
        )
        # Conventions paths -> tmp_path so no real dirs are touched
        stack.enter_context(
            patch("amplifier_distro.conventions.AMPLIFIER_HOME", str(tmp_path / "amp"))
        )
        stack.enter_context(
            patch("amplifier_distro.conventions.DISTRO_HOME", str(tmp_path / "distro"))
        )

        ns = MagicMock()
        ns.mock_uvicorn = mock_uvicorn
        ns.mock_resolve = mock_resolve
        ns.echo_calls = echo_calls
        ns.tmp_path = tmp_path

        yield ns


# ---------------------------------------------------------------------------
# 1. Auto-promotion: serve + 0.0.0.0 + no Tailscale + default tls → "auto"
# ---------------------------------------------------------------------------


class TestAutoTLSRemoteNoTailscale:
    """0.0.0.0 + no Tailscale + no explicit --tls → TLS auto-enabled."""

    def test_resolve_cert_called_with_auto_when_remote_no_tailscale(
        self, foreground_env
    ) -> None:
        """resolve_cert must be called with mode='auto' (promoted from default off)."""
        from amplifier_distro.server.cli import _run_foreground

        _run_foreground(
            host="0.0.0.0",
            port=8080,
            apps_dir=None,
            reload=False,
            dev=True,
            tls_mode="off",
            tls_explicit=False,  # default — not explicitly passed by user
        )

        foreground_env.mock_resolve.assert_called_once_with(
            mode="auto",
            certfile="",
            keyfile="",
        )

    def test_auto_enable_message_printed_when_promoted(self, foreground_env) -> None:
        """The auto-enabling info message must appear in startup output."""
        from amplifier_distro.server.cli import _run_foreground

        _run_foreground(
            host="0.0.0.0",
            port=8080,
            apps_dir=None,
            reload=False,
            dev=True,
            tls_mode="off",
            tls_explicit=False,
        )

        all_output = "\n".join(foreground_env.echo_calls)
        assert "Auto-enabling TLS" in all_output, (
            f"Expected auto-enable message. Got:\n{all_output}"
        )
        assert "Tailscale" in all_output, "Expected Tailscale mention in tip message"

    def test_uvicorn_receives_ssl_args_when_auto_promoted(self, foreground_env) -> None:
        """When auto-promoted, uvicorn must receive SSL args from resolve_cert."""
        from amplifier_distro.server.cli import _run_foreground

        fake_cert = foreground_env.tmp_path / "cert.pem"
        fake_key = foreground_env.tmp_path / "key.pem"
        fake_cert.write_text("cert")
        fake_key.write_text("key")
        foreground_env.mock_resolve.return_value = (fake_cert, fake_key)

        _run_foreground(
            host="0.0.0.0",
            port=8080,
            apps_dir=None,
            reload=False,
            dev=True,
            tls_mode="off",
            tls_explicit=False,
        )

        kwargs = foreground_env.mock_uvicorn.run.call_args.kwargs
        assert "ssl_certfile" in kwargs, "Expected ssl_certfile to be passed to uvicorn"
        assert "ssl_keyfile" in kwargs, "Expected ssl_keyfile to be passed to uvicorn"


# ---------------------------------------------------------------------------
# 2. Escape hatch: explicit --tls off → stays off (not promoted)
# ---------------------------------------------------------------------------


class TestExplicitTLSOffEscapeHatch:
    """When user explicitly passes --tls off, the escape hatch is respected."""

    def test_resolve_cert_called_with_off_when_explicit(self, foreground_env) -> None:
        """resolve_cert must use mode='off' when user explicitly passed --tls off."""
        from amplifier_distro.server.cli import _run_foreground

        _run_foreground(
            host="0.0.0.0",
            port=8080,
            apps_dir=None,
            reload=False,
            dev=True,
            tls_mode="off",
            tls_explicit=True,  # user explicitly passed --tls off
        )

        foreground_env.mock_resolve.assert_called_once_with(
            mode="off",
            certfile="",
            keyfile="",
        )

    def test_no_auto_enable_message_when_explicit_off(self, foreground_env) -> None:
        """No auto-enable message must appear when user explicitly passed --tls off."""
        from amplifier_distro.server.cli import _run_foreground

        _run_foreground(
            host="0.0.0.0",
            port=8080,
            apps_dir=None,
            reload=False,
            dev=True,
            tls_mode="off",
            tls_explicit=True,
        )

        all_output = "\n".join(foreground_env.echo_calls)
        assert "Auto-enabling TLS" not in all_output, (
            "Should NOT auto-enable when user explicitly passed --tls off. "
            f"Got:\n{all_output}"
        )

    def test_uvicorn_no_ssl_args_when_explicit_off(self, foreground_env) -> None:
        """With explicit --tls off, uvicorn must NOT receive ssl args."""
        from amplifier_distro.server.cli import _run_foreground

        _run_foreground(
            host="0.0.0.0",
            port=8080,
            apps_dir=None,
            reload=False,
            dev=True,
            tls_mode="off",
            tls_explicit=True,
        )

        kwargs = foreground_env.mock_uvicorn.run.call_args.kwargs
        assert "ssl_certfile" not in kwargs
        assert "ssl_keyfile" not in kwargs


# ---------------------------------------------------------------------------
# 3. Tailscale active: no self-signed auto-enable (Tailscale handles HTTPS)
# ---------------------------------------------------------------------------


class TestTailscaleActiveNoAutoTLS:
    """When Tailscale is providing HTTPS, no self-signed auto-enable occurs."""

    def test_no_auto_tls_when_tailscale_active(self, foreground_env) -> None:
        """With Tailscale active, resolve_cert must NOT be called at all."""
        from amplifier_distro.server.cli import _run_foreground

        with patch(
            "amplifier_distro.server.cli._setup_tailscale",
            return_value="https://box.ts.net",
        ):
            _run_foreground(
                host="0.0.0.0",
                port=8080,
                apps_dir=None,
                reload=False,
                dev=True,
                tls_mode="off",
                tls_explicit=False,  # default, but Tailscale is active
            )

        foreground_env.mock_resolve.assert_not_called()

    def test_no_auto_enable_message_when_tailscale_active(self, foreground_env) -> None:
        """No self-signed auto-enable message when Tailscale handles HTTPS."""
        from amplifier_distro.server.cli import _run_foreground

        with patch(
            "amplifier_distro.server.cli._setup_tailscale",
            return_value="https://box.ts.net",
        ):
            _run_foreground(
                host="0.0.0.0",
                port=8080,
                apps_dir=None,
                reload=False,
                dev=True,
                tls_mode="off",
                tls_explicit=False,
            )

        all_output = "\n".join(foreground_env.echo_calls)
        assert "Auto-enabling TLS" not in all_output, (
            "Should NOT show auto-enable message when Tailscale is handling HTTPS"
        )


# ---------------------------------------------------------------------------
# 4. Localhost (127.0.0.1): never auto-enables TLS
# ---------------------------------------------------------------------------


class TestLocalhostNoAutoTLS:
    """Localhost binding (127.0.0.1) must never auto-enable TLS."""

    def test_localhost_does_not_auto_promote(self, foreground_env) -> None:
        """resolve_cert must be called with mode='off' for localhost, never auto."""
        from amplifier_distro.server.cli import _run_foreground

        _run_foreground(
            host="127.0.0.1",
            port=8080,
            apps_dir=None,
            reload=False,
            dev=True,
            tls_mode="off",
            tls_explicit=False,  # default, but localhost — should NOT promote
        )

        foreground_env.mock_resolve.assert_called_once_with(
            mode="off",
            certfile="",
            keyfile="",
        )

    def test_no_auto_enable_message_for_localhost(self, foreground_env) -> None:
        """No auto-enable message must appear for localhost binding."""
        from amplifier_distro.server.cli import _run_foreground

        _run_foreground(
            host="127.0.0.1",
            port=8080,
            apps_dir=None,
            reload=False,
            dev=True,
            tls_mode="off",
            tls_explicit=False,
        )

        all_output = "\n".join(foreground_env.echo_calls)
        assert "Auto-enabling TLS" not in all_output, (
            "Should NOT auto-enable TLS for localhost"
        )


# ---------------------------------------------------------------------------
# 5. Explicit --tls auto: works as before, no double-promotion
# ---------------------------------------------------------------------------


class TestExplicitTLSAutoNoDoublePromotion:
    """Explicit --tls auto works as before with no double-promotion."""

    def test_explicit_tls_auto_passes_through(self, foreground_env) -> None:
        """Explicit --tls auto must call resolve_cert with mode='auto' (no change)."""
        from amplifier_distro.server.cli import _run_foreground

        fake_cert = foreground_env.tmp_path / "cert.pem"
        fake_key = foreground_env.tmp_path / "key.pem"
        fake_cert.write_text("cert")
        fake_key.write_text("key")
        foreground_env.mock_resolve.return_value = (fake_cert, fake_key)

        _run_foreground(
            host="0.0.0.0",
            port=8080,
            apps_dir=None,
            reload=False,
            dev=True,
            tls_mode="auto",
            tls_explicit=True,  # user explicitly passed --tls auto
        )

        foreground_env.mock_resolve.assert_called_once_with(
            mode="auto",
            certfile="",
            keyfile="",
        )

    def test_no_double_auto_enable_message_when_already_auto(
        self, foreground_env
    ) -> None:
        """No auto-enable message when user already passed --tls auto explicitly."""
        from amplifier_distro.server.cli import _run_foreground

        _run_foreground(
            host="0.0.0.0",
            port=8080,
            apps_dir=None,
            reload=False,
            dev=True,
            tls_mode="auto",
            tls_explicit=True,
        )

        all_output = "\n".join(foreground_env.echo_calls)
        assert "Auto-enabling TLS" not in all_output, (
            "Should NOT show auto-enable message when --tls auto was explicitly passed"
        )


# ---------------------------------------------------------------------------
# 6. CLI integration: serve_cmd passes tls_explicit correctly
# ---------------------------------------------------------------------------


class TestServeCmdPassesTlsExplicit:
    """serve_cmd in cli.py must pass tls_explicit=True when --tls is provided."""

    def test_default_tls_not_explicit(self) -> None:
        """Without --tls flag, tls_explicit=False must be passed to _run_foreground."""
        from unittest.mock import MagicMock, patch

        from click.testing import CliRunner

        from amplifier_distro.cli import main

        runner = CliRunner()
        mock_run = MagicMock()
        with patch("amplifier_distro.server.cli._run_foreground", mock_run):
            result = runner.invoke(main, ["serve"])
        assert result.exit_code == 0, f"CLI failed:\n{result.output}"
        mock_run.assert_called_once()
        _, kwargs = mock_run.call_args
        val = kwargs.get("tls_explicit")
        assert val is False, (
            f"Expected tls_explicit=False for default --tls, got {val!r}"
        )

    def test_explicit_tls_off_sets_explicit_true(self) -> None:
        """With --tls off, tls_explicit=True must be passed to _run_foreground."""
        from unittest.mock import MagicMock, patch

        from click.testing import CliRunner

        from amplifier_distro.cli import main

        runner = CliRunner()
        mock_run = MagicMock()
        with patch("amplifier_distro.server.cli._run_foreground", mock_run):
            result = runner.invoke(main, ["serve", "--tls", "off"])
        assert result.exit_code == 0, f"CLI failed:\n{result.output}"
        mock_run.assert_called_once()
        _, kwargs = mock_run.call_args
        val = kwargs.get("tls_explicit")
        assert val is True, (
            f"Expected tls_explicit=True for explicit --tls off, got {val!r}"
        )

    def test_explicit_tls_auto_sets_explicit_true(self) -> None:
        """With --tls auto, tls_explicit=True must be passed to _run_foreground."""
        from unittest.mock import MagicMock, patch

        from click.testing import CliRunner

        from amplifier_distro.cli import main

        runner = CliRunner()
        mock_run = MagicMock()
        with patch("amplifier_distro.server.cli._run_foreground", mock_run):
            result = runner.invoke(main, ["serve", "--tls", "auto"])
        assert result.exit_code == 0, f"CLI failed:\n{result.output}"
        mock_run.assert_called_once()
        _, kwargs = mock_run.call_args
        val = kwargs.get("tls_explicit")
        assert val is True, f"Expected tls_explicit=True for --tls auto, got {val!r}"
