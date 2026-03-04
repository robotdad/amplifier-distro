"""Tests for TLS wiring into uvicorn startup.

Validates:
1. uvicorn receives ssl_certfile/ssl_keyfile when tls_mode='auto'
2. uvicorn has no ssl args when tls_mode='off'
3. resolve_cert is called with correct parameters after Tailscale setup
4. Startup echo uses https:// scheme when TLS is active
5. Default tls_mode='off' preserves current HTTP-only behavior
"""

from __future__ import annotations

import contextlib
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Fixture - isolate _run_foreground from real services
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
        # Source-module patches (local imports inside _run_foreground)
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
        # Module-level helpers in cli.py
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
        # click.echo - capture output (click is a top-level import in cli.py)
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

        # Expose useful handles to the test
        ns = MagicMock()
        ns.mock_uvicorn = mock_uvicorn
        ns.mock_resolve = mock_resolve
        ns.echo_calls = echo_calls
        ns.tmp_path = tmp_path

        yield ns


# ---------------------------------------------------------------------------
# Tests - tls_mode='off' (default)
# ---------------------------------------------------------------------------


class TestTLSOffMode:
    """tls_mode='off' (the default) must NOT pass SSL args to uvicorn."""

    def test_uvicorn_no_ssl_args_when_tls_off(self, foreground_env) -> None:
        """uvicorn.run() must NOT receive ssl_certfile/ssl_keyfile."""
        from amplifier_distro.server.cli import _run_foreground

        _run_foreground(
            host="127.0.0.1",
            port=8080,
            apps_dir=None,
            reload=False,
            dev=True,
            tls_mode="off",
        )

        foreground_env.mock_uvicorn.run.assert_called_once()
        kwargs = foreground_env.mock_uvicorn.run.call_args.kwargs
        assert "ssl_certfile" not in kwargs
        assert "ssl_keyfile" not in kwargs

    def test_default_tls_mode_is_off(self, foreground_env) -> None:
        """Calling without tls_mode must default to 'off' (no SSL)."""
        from amplifier_distro.server.cli import _run_foreground

        _run_foreground(
            host="127.0.0.1",
            port=8080,
            apps_dir=None,
            reload=False,
            dev=True,
        )

        foreground_env.mock_uvicorn.run.assert_called_once()
        kwargs = foreground_env.mock_uvicorn.run.call_args.kwargs
        assert "ssl_certfile" not in kwargs
        assert "ssl_keyfile" not in kwargs


# ---------------------------------------------------------------------------
# Tests - tls_mode='auto'
# ---------------------------------------------------------------------------


class TestTLSAutoMode:
    """tls_mode='auto' must pass SSL cert/key paths to uvicorn."""

    def test_uvicorn_receives_ssl_args_when_tls_auto(self, foreground_env) -> None:
        """uvicorn.run() must receive ssl_certfile and ssl_keyfile strings."""
        from amplifier_distro.server.cli import _run_foreground

        fake_cert = foreground_env.tmp_path / "cert.pem"
        fake_key = foreground_env.tmp_path / "key.pem"
        fake_cert.write_text("cert")
        fake_key.write_text("key")
        foreground_env.mock_resolve.return_value = (fake_cert, fake_key)

        _run_foreground(
            host="127.0.0.1",
            port=8080,
            apps_dir=None,
            reload=False,
            dev=True,
            tls_mode="auto",
        )

        foreground_env.mock_uvicorn.run.assert_called_once()
        kwargs = foreground_env.mock_uvicorn.run.call_args.kwargs
        assert kwargs["ssl_certfile"] == str(fake_cert)
        assert kwargs["ssl_keyfile"] == str(fake_key)

    def test_uvicorn_receives_ssl_args_in_reload_mode(self, foreground_env) -> None:
        """The reload-mode uvicorn.run() call also gets ssl kwargs."""
        from amplifier_distro.server.cli import _run_foreground

        fake_cert = foreground_env.tmp_path / "cert.pem"
        fake_key = foreground_env.tmp_path / "key.pem"
        fake_cert.write_text("cert")
        fake_key.write_text("key")
        foreground_env.mock_resolve.return_value = (fake_cert, fake_key)

        _run_foreground(
            host="127.0.0.1",
            port=8080,
            apps_dir=None,
            reload=True,
            dev=True,
            tls_mode="auto",
        )

        foreground_env.mock_uvicorn.run.assert_called_once()
        kwargs = foreground_env.mock_uvicorn.run.call_args.kwargs
        assert kwargs["ssl_certfile"] == str(fake_cert)
        assert kwargs["ssl_keyfile"] == str(fake_key)

    def test_resolve_cert_called_with_correct_params(self, foreground_env) -> None:
        """resolve_cert must receive mode, certfile, keyfile from parameters."""
        from amplifier_distro.server.cli import _run_foreground

        _run_foreground(
            host="127.0.0.1",
            port=8080,
            apps_dir=None,
            reload=False,
            dev=True,
            tls_mode="auto",
            ssl_certfile="/path/to/cert.pem",
            ssl_keyfile="/path/to/key.pem",
        )

        foreground_env.mock_resolve.assert_called_once_with(
            mode="auto",
            certfile="/path/to/cert.pem",
            keyfile="/path/to/key.pem",
        )


# ---------------------------------------------------------------------------
# Tests - startup echo scheme
# ---------------------------------------------------------------------------


class TestTLSStartupEcho:
    """Startup echo must use https:// scheme when TLS is active."""

    def test_startup_echo_uses_https_when_tls_active(self, foreground_env) -> None:
        """The 'Local:' line should show https:// when TLS is on."""
        from amplifier_distro.server.cli import _run_foreground

        fake_cert = foreground_env.tmp_path / "cert.pem"
        fake_key = foreground_env.tmp_path / "key.pem"
        fake_cert.write_text("cert")
        fake_key.write_text("key")
        foreground_env.mock_resolve.return_value = (fake_cert, fake_key)

        _run_foreground(
            host="127.0.0.1",
            port=8080,
            apps_dir=None,
            reload=False,
            dev=True,
            tls_mode="auto",
        )

        local_lines = [line for line in foreground_env.echo_calls if "Local:" in line]
        assert local_lines, "Expected a 'Local:' line in startup output"
        assert "https://" in local_lines[0]

    def test_startup_echo_uses_http_when_tls_off(self, foreground_env) -> None:
        """The 'Local:' line should show http:// when TLS is off."""
        from amplifier_distro.server.cli import _run_foreground

        _run_foreground(
            host="127.0.0.1",
            port=8080,
            apps_dir=None,
            reload=False,
            dev=True,
            tls_mode="off",
        )

        local_lines = [line for line in foreground_env.echo_calls if "Local:" in line]
        assert local_lines, "Expected a 'Local:' line in startup output"
        assert "http://" in local_lines[0]
        assert "https://" not in local_lines[0]


# ---------------------------------------------------------------------------
# Tests - tailscale serve active (Issue 2)
# ---------------------------------------------------------------------------


class TestTailscaleServeSkipsNativeTLS:
    """When tailscale serve is active, native TLS must NOT be enabled."""

    def test_uvicorn_has_no_ssl_when_tailscale_serve_active(
        self, foreground_env
    ) -> None:
        """When tailscale serve is active, no ssl args even with tls_mode=auto.

        This is the critical regression test: certs ARE available (resolve_cert
        would return paths), but because tailscale serve is handling HTTPS as a
        reverse proxy, uvicorn must run plain HTTP so tailscale can reach it.
        """
        from unittest.mock import patch

        from amplifier_distro.server.cli import _run_foreground

        # Certs ARE available — if the bug were present, ssl args would be passed
        fake_cert = foreground_env.tmp_path / "cert.pem"
        fake_key = foreground_env.tmp_path / "key.pem"
        fake_cert.write_text("cert")
        fake_key.write_text("key")
        foreground_env.mock_resolve.return_value = (fake_cert, fake_key)

        # _setup_tailscale returns a URL → tailscale serve is active
        with patch(
            "amplifier_distro.server.cli._setup_tailscale",
            return_value="https://box.ts.net",
        ):
            _run_foreground(
                host="127.0.0.1",
                port=8080,
                apps_dir=None,
                reload=False,
                dev=True,
                tls_mode="auto",
            )

        foreground_env.mock_uvicorn.run.assert_called_once()
        kwargs = foreground_env.mock_uvicorn.run.call_args.kwargs
        assert "ssl_certfile" not in kwargs, (
            "ssl_certfile should NOT be passed when tailscale serve is active"
        )
        assert "ssl_keyfile" not in kwargs, (
            "ssl_keyfile should NOT be passed when tailscale serve is active"
        )

    def test_resolve_cert_not_called_when_tailscale_serve_active(
        self, foreground_env
    ) -> None:
        """resolve_cert must NOT be called when tailscale serve provides HTTPS."""
        from unittest.mock import patch

        from amplifier_distro.server.cli import _run_foreground

        with patch(
            "amplifier_distro.server.cli._setup_tailscale",
            return_value="https://box.ts.net",
        ):
            _run_foreground(
                host="127.0.0.1",
                port=8080,
                apps_dir=None,
                reload=False,
                dev=True,
                tls_mode="auto",
            )

        foreground_env.mock_resolve.assert_not_called()

    def test_startup_echo_shows_https_when_tailscale_serve_active(
        self, foreground_env
    ) -> None:
        """Startup output should indicate HTTPS when tailscale serve is active."""
        from unittest.mock import patch

        from amplifier_distro.server.cli import _run_foreground

        with patch(
            "amplifier_distro.server.cli._setup_tailscale",
            return_value="https://box.ts.net",
        ):
            _run_foreground(
                host="127.0.0.1",
                port=8080,
                apps_dir=None,
                reload=False,
                dev=True,
                tls_mode="auto",
            )

        # Should echo about tailscale providing HTTPS
        all_output = "\n".join(foreground_env.echo_calls)
        assert "Tailscale" in all_output or "tailscale" in all_output.lower()

    def test_tailscale_url_shown_prominently_with_default_tls_off(
        self, foreground_env
    ) -> None:
        """When Tailscale is active with default tls_mode='off', the green HTTPS
        confirmation must always appear — not be gated behind tls_mode != 'off'.

        This is the regression test for the bug where the prominent green block
        was hidden from users who never passed --tls (the vast majority).
        """
        from unittest.mock import patch

        from amplifier_distro.server.cli import _run_foreground

        with patch(
            "amplifier_distro.server.cli._setup_tailscale",
            return_value="https://monad.tail09557f.ts.net",
        ):
            _run_foreground(
                host="127.0.0.1",
                port=8080,
                apps_dir=None,
                reload=False,
                dev=True,
                tls_mode="off",  # default — the bug case
            )

        all_output = "\n".join(foreground_env.echo_calls)
        # The prominent green confirmation must appear regardless of tls_mode
        assert "✓ HTTPS provided by Tailscale" in all_output, (
            "Tailscale HTTPS confirmation should be shown even with tls_mode='off'. "
            f"Actual output:\n{all_output}"
        )
        # And the URL itself must appear
        assert "https://monad.tail09557f.ts.net" in all_output, (
            "Tailscale HTTPS URL should always be shown in startup output"
        )


# ---------------------------------------------------------------------------
# Tests - auth secret wired correctly
# ---------------------------------------------------------------------------


class TestAuthSetup:
    """Auth is activated when TLS + Linux platform conditions are met."""

    def test_auth_secret_set_when_tls_active_on_linux(self, foreground_env) -> None:
        """create_server must be called with a non-empty auth_secret when TLS is
        active on Linux.  This catches the bug where platform was never passed
        to is_auth_applicable() so it defaulted to None and auth was dead code.
        """
        from unittest.mock import MagicMock, patch

        from amplifier_distro.server.cli import _run_foreground

        fake_cert = foreground_env.tmp_path / "cert.pem"
        fake_key = foreground_env.tmp_path / "key.pem"
        fake_cert.write_text("cert")
        fake_key.write_text("key")
        foreground_env.mock_resolve.return_value = (fake_cert, fake_key)

        mock_get_secret = MagicMock(return_value="test-secret-value")

        with (
            patch("sys.platform", "linux"),
            patch("amplifier_distro.server.auth.get_or_create_secret", mock_get_secret),
        ):
            _run_foreground(
                host="127.0.0.1",
                port=8080,
                apps_dir=None,
                reload=False,
                dev=True,
                tls_mode="auto",
            )

        # get_or_create_secret must have been called — auth was activated
        mock_get_secret.assert_called_once()

    def test_no_auth_skips_auth_even_with_tls_on_linux(self, foreground_env) -> None:
        """With no_auth=True, auth must NOT be set up even when TLS+Linux is active."""
        from unittest.mock import MagicMock, patch

        from amplifier_distro.server.cli import _run_foreground

        fake_cert = foreground_env.tmp_path / "cert.pem"
        fake_key = foreground_env.tmp_path / "key.pem"
        fake_cert.write_text("cert")
        fake_key.write_text("key")
        foreground_env.mock_resolve.return_value = (fake_cert, fake_key)

        mock_get_secret = MagicMock(return_value="test-secret-value")

        with (
            patch("sys.platform", "linux"),
            patch("amplifier_distro.server.auth.get_or_create_secret", mock_get_secret),
        ):
            _run_foreground(
                host="127.0.0.1",
                port=8080,
                apps_dir=None,
                reload=False,
                dev=True,
                tls_mode="auto",
                no_auth=True,
            )

        # get_or_create_secret must NOT have been called — auth was bypassed
        mock_get_secret.assert_not_called()
