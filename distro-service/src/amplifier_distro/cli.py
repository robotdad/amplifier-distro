"""Amplifier Distro CLI — thin wrapper around amplifierd with remote-access defaults.

Distro defaults differ from bare amplifierd:
- host: 0.0.0.0 (not 127.0.0.1) — remote-ready
- tls: auto (not off) — HTTPS by default
- auth: enabled (not disabled) — PAM login for remote users

All flags are passed through to amplifierd's serve command.
"""

from __future__ import annotations

import os

import click


@click.group()
def main() -> None:
    """amp-distro — Amplifier distro experience server."""


@main.command()
@click.option("--host", default="0.0.0.0", help="Bind host [default: 0.0.0.0].")  # noqa: S104
@click.option("--port", default=None, type=int, help="Bind port number.")
@click.option(
    "--tls",
    "tls_mode",
    default="auto",
    type=click.Choice(["auto", "off", "manual"], case_sensitive=False),
    help="TLS mode [default: auto].",
)
@click.option("--no-auth", is_flag=True, default=False, help="Disable authentication.")
@click.option(
    "--reload", is_flag=True, default=False, help="Enable hot-reload for development."
)
@click.option("--log-level", default=None, help="Log level: debug|info|warning|error.")
@click.pass_context
def serve(
    ctx: click.Context,
    host: str,
    port: int | None,
    tls_mode: str,
    no_auth: bool,
    reload: bool,
    log_level: str | None,
) -> None:
    """Start the distro experience server with remote-access defaults."""
    # Set distro defaults via env — amplifierd's DaemonSettings picks these up
    os.environ.setdefault("AMPLIFIERD_TLS_MODE", tls_mode)
    if not no_auth:
        os.environ.setdefault("AMPLIFIERD_AUTH_ENABLED", "true")
    else:
        os.environ["AMPLIFIERD_AUTH_ENABLED"] = "false"

    # Delegate to amplifierd's serve command
    from amplifierd.cli import serve as amplifierd_serve

    ctx.invoke(
        amplifierd_serve,
        host=host,
        port=port,
        reload=reload,
        log_level=log_level,
        # These pass through as None — amplifierd picks up env vars via DaemonSettings
        bundle=(),
        default_bundle=None,
        api_key=None,
        tls_mode=None,
        ssl_certfile=None,
        ssl_keyfile=None,
        no_auth=False,
    )
