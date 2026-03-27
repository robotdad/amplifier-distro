"""Tests for CLI server startup logic."""

from __future__ import annotations

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
