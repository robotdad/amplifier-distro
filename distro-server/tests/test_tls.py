"""Tests for TLS certificate generation and resolution.

Validates:
1. generate_self_signed_cert creates cert and key files
2. Files end with .pem
3. Cert is loadable by ssl.SSLContext.load_cert_chain
4. Reuses existing cert (same mtime)
5. Creates cert_dir if missing
6. resolve_cert dispatches by mode (off, manual, auto)
"""

from __future__ import annotations

import os
import ssl
from pathlib import Path
from unittest.mock import patch

from amplifier_distro.server.tls import generate_self_signed_cert, resolve_cert


class TestGenerateSelfSignedCert:
    def test_creates_cert_and_key_files(self, tmp_path: Path) -> None:
        cert_path, key_path = generate_self_signed_cert(tmp_path)
        assert cert_path.exists()
        assert key_path.exists()

    def test_files_end_with_pem(self, tmp_path: Path) -> None:
        cert_path, key_path = generate_self_signed_cert(tmp_path)
        assert cert_path.suffix == ".pem"
        assert key_path.suffix == ".pem"

    def test_cert_loadable_by_ssl_context(self, tmp_path: Path) -> None:
        cert_path, key_path = generate_self_signed_cert(tmp_path)
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.load_cert_chain(certfile=str(cert_path), keyfile=str(key_path))

    def test_reuses_existing_cert(self, tmp_path: Path) -> None:
        cert_path_1, key_path_1 = generate_self_signed_cert(tmp_path)
        mtime_cert = cert_path_1.stat().st_mtime
        mtime_key = key_path_1.stat().st_mtime

        cert_path_2, key_path_2 = generate_self_signed_cert(tmp_path)
        assert cert_path_2.stat().st_mtime == mtime_cert
        assert key_path_2.stat().st_mtime == mtime_key

    def test_creates_cert_dir_if_missing(self, tmp_path: Path) -> None:
        nested = tmp_path / "deep" / "nested" / "certs"
        assert not nested.exists()
        cert_path, key_path = generate_self_signed_cert(nested)
        assert nested.is_dir()
        assert cert_path.exists()
        assert key_path.exists()

    def test_key_file_permissions(self, tmp_path: Path) -> None:
        _cert_path, key_path = generate_self_signed_cert(tmp_path)
        mode = os.stat(key_path).st_mode & 0o777
        assert mode == 0o600

    def test_cert_file_names(self, tmp_path: Path) -> None:
        cert_path, key_path = generate_self_signed_cert(tmp_path)
        assert cert_path.name == "self-signed.pem"
        assert key_path.name == "self-signed-key.pem"

    def test_returns_paths_in_cert_dir(self, tmp_path: Path) -> None:
        cert_path, key_path = generate_self_signed_cert(tmp_path)
        assert cert_path.parent == tmp_path
        assert key_path.parent == tmp_path


# ---------------------------------------------------------------------------
# resolve_cert
# ---------------------------------------------------------------------------


class TestResolveCert:
    """Tests for resolve_cert() dispatch logic."""

    # -- off mode --

    def test_off_mode_returns_none(self) -> None:
        result = resolve_cert(mode="off")
        assert result is None

    # -- manual mode --

    def test_manual_returns_paths_if_they_exist(self, tmp_path: Path) -> None:
        cert = tmp_path / "cert.pem"
        key = tmp_path / "key.pem"
        cert.write_text("cert")
        key.write_text("key")

        result = resolve_cert(mode="manual", certfile=str(cert), keyfile=str(key))
        assert result == (cert, key)

    def test_manual_returns_none_if_cert_missing(self, tmp_path: Path) -> None:
        key = tmp_path / "key.pem"
        key.write_text("key")
        missing_cert = tmp_path / "no-cert.pem"

        result = resolve_cert(
            mode="manual", certfile=str(missing_cert), keyfile=str(key)
        )
        assert result is None

    def test_manual_returns_none_if_key_missing(self, tmp_path: Path) -> None:
        cert = tmp_path / "cert.pem"
        cert.write_text("cert")
        missing_key = tmp_path / "no-key.pem"

        result = resolve_cert(
            mode="manual", certfile=str(cert), keyfile=str(missing_key)
        )
        assert result is None

    def test_manual_logs_error_when_cert_missing(self, tmp_path: Path) -> None:
        key = tmp_path / "key.pem"
        key.write_text("key")
        missing_cert = tmp_path / "no-cert.pem"

        with patch("amplifier_distro.server.tls.logger") as mock_logger:
            resolve_cert(mode="manual", certfile=str(missing_cert), keyfile=str(key))
            mock_logger.error.assert_called_once()

    # -- auto mode --

    def test_auto_tries_tailscale_first(self, tmp_path: Path) -> None:
        ts_cert = tmp_path / "ts.crt"
        ts_key = tmp_path / "ts.key"

        with patch(
            "amplifier_distro.server.tls.tailscale.provision_cert",
            return_value=(ts_cert, ts_key),
        ) as mock_ts:
            result = resolve_cert(mode="auto", cert_dir=tmp_path)
            assert result == (ts_cert, ts_key)
            mock_ts.assert_called_once_with(tmp_path)

    def test_auto_falls_back_to_self_signed(self, tmp_path: Path) -> None:
        with patch(
            "amplifier_distro.server.tls.tailscale.provision_cert",
            return_value=None,
        ):
            result = resolve_cert(mode="auto", cert_dir=tmp_path)
            assert result is not None
            cert_path, key_path = result
            assert cert_path.exists()
            assert key_path.exists()

    def test_auto_self_signed_fallback_no_operator_hint(
        self, tmp_path: Path, capsys
    ) -> None:
        """Self-signed fallback should NOT suggest sudo tailscale set --operator."""
        with patch(
            "amplifier_distro.server.tls.tailscale.provision_cert",
            return_value=None,
        ):
            resolve_cert(mode="auto", cert_dir=tmp_path)
            captured = capsys.readouterr()
            assert "--operator" not in captured.out

    def test_auto_self_signed_fallback_shows_admin_console_url(
        self, tmp_path: Path, capsys
    ) -> None:
        """Self-signed fallback should show direct admin console URL."""
        with patch(
            "amplifier_distro.server.tls.tailscale.provision_cert",
            return_value=None,
        ):
            resolve_cert(mode="auto", cert_dir=tmp_path)
            captured = capsys.readouterr()
            assert "https://login.tailscale.com/admin/dns" in captured.out

    def test_auto_uses_default_cert_dir(self) -> None:
        """When cert_dir is None, auto mode uses conventions.DISTRO_CERTS_DIR."""
        ts_cert = Path("/tmp/ts.crt")
        ts_key = Path("/tmp/ts.key")

        with (
            patch(
                "amplifier_distro.server.tls.tailscale.provision_cert",
                return_value=(ts_cert, ts_key),
            ) as mock_ts,
            patch(
                "amplifier_distro.server.tls.conventions.DISTRO_CERTS_DIR",
                "/tmp/test-certs",
            ),
        ):
            resolve_cert(mode="auto")
            mock_ts.assert_called_once_with(Path("/tmp/test-certs"))
