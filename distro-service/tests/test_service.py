"""Tests for service template generators."""

from __future__ import annotations

from amplifier_distro.service import (
    _generate_launchd_server_plist,
    _generate_systemd_server_unit,
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
