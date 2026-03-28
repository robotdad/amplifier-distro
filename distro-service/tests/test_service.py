"""Tests for service template generators."""

from __future__ import annotations

from unittest.mock import patch

from click.testing import CliRunner

from amplifier_distro.cli import main
from amplifier_distro.service import (
    ServiceResult,
    _generate_launchd_server_plist,
    _generate_systemd_server_unit,
    _generate_systemd_watchdog_unit,  # noqa: F401 — required by spec (task-4); not used until watchdog template tests are added
)


class TestSystemdServerTemplate:
    """Tests for the systemd unit file generator."""

    def test_systemd_template_does_not_use_serve_subcommand(self) -> None:
        """ExecStart must not include the removed 'serve' subcommand."""
        unit = _generate_systemd_server_unit("/usr/bin/amp-distro", "0.0.0.0", 8410)
        exec_start_line = [
            line for line in unit.splitlines() if line.strip().startswith("ExecStart=")
        ][0]
        assert "serve" not in exec_start_line

    def test_systemd_template_includes_host_and_port(self) -> None:
        """ExecStart must pass --host and --port arguments."""
        unit = _generate_systemd_server_unit("/usr/bin/amp-distro", "0.0.0.0", 8410)
        exec_start_line = [
            line for line in unit.splitlines() if line.strip().startswith("ExecStart=")
        ][0]
        assert "--host 0.0.0.0" in exec_start_line
        assert "--port 8410" in exec_start_line


class TestLaunchdServerPlist:
    """Tests for the launchd plist generator."""

    def test_launchd_plist_does_not_use_serve_subcommand(self) -> None:
        """ProgramArguments must not include the removed 'serve' subcommand."""
        plist = _generate_launchd_server_plist(
            "/usr/local/bin/amp-distro", "0.0.0.0", 8410
        )
        assert "<string>serve</string>" not in plist

    def test_launchd_plist_includes_host_and_port(self) -> None:
        """ProgramArguments must pass --host and --port arguments."""
        plist = _generate_launchd_server_plist(
            "/usr/local/bin/amp-distro", "0.0.0.0", 8410
        )
        assert "<string>--host</string>" in plist
        assert "<string>0.0.0.0</string>" in plist
        assert "<string>--port</string>" in plist
        assert "<string>8410</string>" in plist


class TestServiceInstallCLI:
    """Tests for the service install CLI command.

    These tests define the DESIRED behavior of `amp-distro service install`:
    - Default host should be 127.0.0.1 (localhost-only mode)
    - The command should accept a --tls flag
    - tls_mode should be forwarded to install_service (default: None)

    All three tests are expected to FAIL against the current implementation because:
    1. service install currently defaults host to 0.0.0.0, not 127.0.0.1
    2. service install has no --tls flag
    3. install_service does not accept a tls_mode parameter
    """

    def test_service_install_default_host_is_localhost(self) -> None:
        """Default host for 'service install' should be 127.0.0.1 (localhost).

        Expected to FAIL: current default is 0.0.0.0.
        """
        captured: dict = {}

        def fake_install_service(**kwargs: object) -> ServiceResult:
            captured.update(kwargs)
            return ServiceResult(success=True, platform="linux", message="Mocked")

        runner = CliRunner()
        with patch("amplifier_distro.service.install_service", fake_install_service):
            runner.invoke(main, ["service", "install"])

        assert captured.get("host") == "127.0.0.1"

    def test_service_install_accepts_tls_flag(self) -> None:
        """service install should accept --tls off and pass tls_mode to install_service.

        Expected to FAIL: service install currently has no --tls flag.
        """
        captured: dict = {}

        def fake_install_service(**kwargs: object) -> ServiceResult:
            captured.update(kwargs)
            return ServiceResult(success=True, platform="linux", message="Mocked")

        runner = CliRunner()
        with patch("amplifier_distro.service.install_service", fake_install_service):
            runner.invoke(
                main, ["service", "install", "--host", "0.0.0.0", "--tls", "off"]
            )

        assert captured.get("tls_mode") == "off"
        assert captured.get("host") == "0.0.0.0"

    def test_service_install_tls_default_is_none(self) -> None:
        """service install without --tls should pass tls_mode=None to install_service.

        Expected to FAIL: install_service does not currently accept tls_mode.
        """
        captured: dict = {}

        def fake_install_service(**kwargs: object) -> ServiceResult:
            captured.update(kwargs)
            return ServiceResult(success=True, platform="linux", message="Mocked")

        runner = CliRunner()
        with patch("amplifier_distro.service.install_service", fake_install_service):
            runner.invoke(main, ["service", "install"])

        # Use direct key access (not .get()) so this fails with KeyError if tls_mode
        # is never passed — a silent None from .get() would produce a false green assertion.
        assert captured["tls_mode"] is None
