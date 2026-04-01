"""Tests for service template generators."""

from __future__ import annotations

from unittest.mock import patch

from click.testing import CliRunner

from amplifier_distro.cli import main
from amplifier_distro.service import (
    ServiceResult,
    _generate_launchd_server_plist,
    _generate_systemd_server_unit,
    _generate_systemd_watchdog_unit,
    _launchd_server_plist_path,
    _status_launchd,
    _status_systemd,
    _systemd_server_unit_path,
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

    Verifies the behavior of `amp-distro service install`:
    - Default host is 127.0.0.1 (localhost-only mode)
    - The command accepts a --tls flag
    - tls_mode is forwarded to install_service (default: None)
    """

    def test_service_install_default_host_is_localhost(self) -> None:
        """Default host for 'service install' should be 127.0.0.1 (localhost)."""
        captured: dict = {}

        def fake_install_service(**kwargs: object) -> ServiceResult:
            captured.update(kwargs)
            return ServiceResult(success=True, platform="linux", message="Mocked")

        runner = CliRunner()
        with patch("amplifier_distro.service.install_service", fake_install_service):
            runner.invoke(main, ["service", "install"])

        assert captured.get("host") == "127.0.0.1"

    def test_service_install_accepts_tls_flag(self) -> None:
        """service install should accept --tls off and pass tls_mode to install_service."""
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
        """service install without --tls should pass tls_mode=None to install_service."""
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


class TestSystemdServerUnitGeneration:
    """Tests for the updated systemd server unit generator.

    Verifies the behavior of _generate_systemd_server_unit:
    - ExecStart must not include 'amp-distro serve' (the removed subcommand)
    - Default host should be 127.0.0.1 (localhost-only mode)
    - tls_mode='off' should append '--tls off' to ExecStart
    - tls_mode=None should omit '--tls' from ExecStart entirely
    - EnvironmentFile must be present pointing to .amplifier/.env
    """

    def test_no_serve_in_exec_start(self) -> None:
        """ExecStart must not contain 'amp-distro serve' and must include correct invocation."""
        unit = _generate_systemd_server_unit("/usr/bin/amp-distro", "127.0.0.1", 8410)
        assert "amp-distro serve" not in unit
        assert "ExecStart=/usr/bin/amp-distro --host 127.0.0.1 --port 8410" in unit

    def test_exec_start_default_localhost(self) -> None:
        """ExecStart must bind to 127.0.0.1 when localhost is specified."""
        unit = _generate_systemd_server_unit("/usr/bin/amp-distro", "127.0.0.1", 8410)
        assert "--host 127.0.0.1" in unit
        assert "--port 8410" in unit

    def test_exec_start_network_host(self) -> None:
        """ExecStart must use the provided network host address."""
        unit = _generate_systemd_server_unit("/usr/bin/amp-distro", "0.0.0.0", 8410)
        assert "--host 0.0.0.0" in unit
        assert "--port 8410" in unit

    def test_exec_start_with_tls_off(self) -> None:
        """When tls_mode='off', ExecStart must include '--tls off'."""
        unit = _generate_systemd_server_unit(
            "/usr/bin/amp-distro", "127.0.0.1", 8410, tls_mode="off"
        )
        assert "--tls off" in unit

    def test_exec_start_without_tls_when_none(self) -> None:
        """When tls_mode=None (default), ExecStart must not include '--tls'."""
        unit = _generate_systemd_server_unit(
            "/usr/bin/amp-distro", "127.0.0.1", 8410, tls_mode=None
        )
        assert "--tls" not in unit

    def test_environment_file_present(self) -> None:
        """Unit must include EnvironmentFile pointing to .amplifier/.env."""
        unit = _generate_systemd_server_unit("/usr/bin/amp-distro", "127.0.0.1", 8410)
        assert "EnvironmentFile=-" in unit
        assert ".amplifier/.env" in unit


class TestSystemdWatchdogUnitGeneration:
    """Tests for the systemd watchdog unit generator.

    The watchdog unit should invoke the 'watchdog' subcommand (not 'serve')
    and mirror the host/port passed to the generator.
    """

    def test_no_serve_in_watchdog_exec_start(self) -> None:
        """Watchdog ExecStart must not contain 'serve' but must contain 'watchdog'."""
        unit = _generate_systemd_watchdog_unit("/usr/bin/amp-distro", "127.0.0.1", 8410)
        assert "serve" not in unit
        assert "watchdog" in unit

    def test_watchdog_mirrors_host_port(self) -> None:
        """Watchdog ExecStart must pass through the specified host and port."""
        unit = _generate_systemd_watchdog_unit("/usr/bin/amp-distro", "0.0.0.0", 9999)
        assert "--host 0.0.0.0" in unit
        assert "--port 9999" in unit


class TestStaleServeDetection:
    """Tests for stale 'serve' subcommand detection in _status_systemd and _status_launchd.

    Verifies the behavior of stale-config detection:
    - When the installed unit file uses the stale 'serve' subcommand in ExecStart,
      a warning containing 'uninstall' or 'reinstall' must appear in the details.
    - When the unit file uses the current format (no 'serve' subcommand), no such
      warning appears.
    """

    def test_detects_stale_serve_in_systemd_unit(self) -> None:
        """_status_systemd must warn when unit file uses the stale 'serve' subcommand."""
        unit_path = _systemd_server_unit_path()
        unit_path.parent.mkdir(parents=True, exist_ok=True)
        stale_content = (
            "[Unit]\n"
            "Description=Amplifier Distro Server\n"
            "[Service]\n"
            "ExecStart=/home/user/.local/bin/amp-distro serve --host 0.0.0.0 --port 8410\n"
        )
        try:
            unit_path.write_text(stale_content)
            with patch(
                "amplifier_distro.service._run_cmd", return_value=(True, "active")
            ):
                result = _status_systemd()
            serve_warnings = [d for d in result.details if "serve" in d]
            assert len(serve_warnings) > 0
            assert any("uninstall" in w or "reinstall" in w for w in serve_warnings)
        finally:
            if unit_path.exists():
                unit_path.unlink()

    def test_no_warning_for_current_format(self) -> None:
        """_status_systemd must not warn when unit file uses the current format (no 'serve').

        When ExecStart does not include the 'serve' subcommand, the details list must
        contain zero entries that mention both 'serve' and 'uninstall'/'reinstall'.
        """
        unit_path = _systemd_server_unit_path()
        unit_path.parent.mkdir(parents=True, exist_ok=True)
        current_content = (
            "[Unit]\n"
            "Description=Amplifier Distro Server\n"
            "[Service]\n"
            "ExecStart=/home/user/.local/bin/amp-distro --host 127.0.0.1 --port 8410\n"
        )
        try:
            unit_path.write_text(current_content)
            with patch(
                "amplifier_distro.service._run_cmd", return_value=(True, "active")
            ):
                result = _status_systemd()
            serve_subcommand_warnings = [
                d
                for d in result.details
                if "serve" in d and ("uninstall" in d or "reinstall" in d)
            ]
            assert len(serve_subcommand_warnings) == 0
        finally:
            if unit_path.exists():
                unit_path.unlink()

    def test_detects_stale_serve_in_launchd_plist(self) -> None:
        """_status_launchd must warn when plist uses the stale 'serve' subcommand."""
        plist_path = _launchd_server_plist_path()
        plist_path.parent.mkdir(parents=True, exist_ok=True)
        stale_content = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<plist version="1.0"><dict>\n'
            "<key>ProgramArguments</key>\n"
            "<array>\n"
            "<string>/home/user/.local/bin/amp-distro serve --host 0.0.0.0 --port 8410</string>\n"
            "</array>\n"
            "</dict></plist>\n"
        )
        try:
            plist_path.write_text(stale_content)
            with patch("amplifier_distro.service._run_cmd", return_value=(True, "")):
                result = _status_launchd()
            serve_warnings = [d for d in result.details if "serve" in d]
            assert len(serve_warnings) > 0
            assert any("uninstall" in w or "reinstall" in w for w in serve_warnings)
        finally:
            if plist_path.exists():
                plist_path.unlink()

    def test_no_warning_for_current_launchd_format(self) -> None:
        """_status_launchd must not warn when plist uses the current format (no 'serve')."""
        plist_path = _launchd_server_plist_path()
        plist_path.parent.mkdir(parents=True, exist_ok=True)
        current_content = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<plist version="1.0"><dict>\n'
            "<key>ProgramArguments</key>\n"
            "<array>\n"
            "<string>/home/user/.local/bin/amp-distro</string>\n"
            "<string>--host</string><string>127.0.0.1</string>\n"
            "</array>\n"
            "</dict></plist>\n"
        )
        try:
            plist_path.write_text(current_content)
            with patch("amplifier_distro.service._run_cmd", return_value=(True, "")):
                result = _status_launchd()
            serve_subcommand_warnings = [
                d
                for d in result.details
                if "serve" in d and ("uninstall" in d or "reinstall" in d)
            ]
            assert len(serve_subcommand_warnings) == 0
        finally:
            if plist_path.exists():
                plist_path.unlink()
