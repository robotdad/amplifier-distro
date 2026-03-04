"""CLI entry point for the distro server.

Usage:
    amp-distro serve [OPTIONS]                # foreground mode (default)
    amp-distro serve start [OPTIONS]          # start as background daemon
    amp-distro serve stop                     # stop background daemon
    amp-distro serve restart [OPTIONS]        # restart background daemon
    amp-distro serve status                   # check daemon status
    amp-distro serve watchdog start           # start health watchdog
    amp-distro serve watchdog stop            # stop health watchdog
    amp-distro serve watchdog status          # check watchdog status
    python -m amplifier_distro.server [OPTIONS]  # via module (foreground)
"""

from __future__ import annotations

import socket
import sys
from pathlib import Path
from typing import Any

import click

from amplifier_distro import conventions


@click.group("amp-distro-serve", invoke_without_command=True)
@click.option(
    "--host",
    default="0.0.0.0",
    help="Bind host (use 127.0.0.1 to restrict to localhost)",
)
@click.option(
    "--port",
    default=conventions.SERVER_DEFAULT_PORT,
    type=int,
    help="Bind port",
)
@click.option(
    "--apps-dir",
    default=None,
    type=click.Path(exists=True),
    help="Apps directory",
)
@click.option("--reload", is_flag=True, help="Enable auto-reload for development")
@click.option(
    "--dev",
    is_flag=True,
    help="Dev mode: mock session backend (no LLM)",
)
@click.option(
    "--stub",
    is_flag=True,
    help="Stub mode: serve UI with canned data for fast iteration (implies --dev)",
)
@click.option(
    "--no-browser",
    is_flag=True,
    help="Don't auto-open browser on startup",
)
@click.option(
    "--tls",
    "tls_mode",
    type=click.Choice(["auto", "off", "manual"], case_sensitive=False),
    default="off",
    help="TLS mode: auto (self-signed), off (plain HTTP), manual (provide certs)",
)
@click.option(
    "--ssl-certfile",
    default="",
    help="Path to SSL certificate file (implies --tls manual)",
)
@click.option(
    "--ssl-keyfile",
    default="",
    help="Path to SSL private key file (used with --ssl-certfile)",
)
@click.option(
    "--no-auth",
    is_flag=True,
    help="Disable authentication",
)
@click.pass_context
def serve(
    ctx: click.Context,
    host: str,
    port: int,
    apps_dir: str | None,
    reload: bool,
    dev: bool,
    stub: bool,
    no_browser: bool,
    tls_mode: str,
    ssl_certfile: str,
    ssl_keyfile: str,
    no_auth: bool,
) -> None:
    """Amplifier distro server.

    Run without a subcommand for foreground mode, or use
    start/stop/restart/status for daemon management.
    """
    ctx.ensure_object(dict)
    if stub:
        dev = True  # stub implies dev
    # --ssl-certfile implies manual TLS mode when tls_mode is still default
    if tls_mode == "off" and ssl_certfile:
        tls_mode = "manual"
    if ctx.invoked_subcommand is None:
        _run_foreground(
            host,
            port,
            apps_dir,
            reload,
            dev,
            stub=stub,
            no_browser=no_browser,
            tls_mode=tls_mode,
            ssl_certfile=ssl_certfile,
            ssl_keyfile=ssl_keyfile,
            no_auth=no_auth,
        )


@serve.command()
@click.option(
    "--host",
    default="0.0.0.0",
    help="Bind host (use 127.0.0.1 to restrict to localhost)",
)
@click.option(
    "--port",
    default=conventions.SERVER_DEFAULT_PORT,
    type=int,
    help="Bind port",
)
@click.option("--apps-dir", default=None, help="Apps directory")
@click.option(
    "--dev",
    is_flag=True,
    help="Dev mode: mock session backend (no LLM)",
)
def start(host: str, port: int, apps_dir: str | None, dev: bool) -> None:
    """Start the server as a background daemon."""
    from amplifier_distro.server.daemon import (
        daemonize,
        is_running,
        pid_file_path,
        wait_for_health,
    )
    from amplifier_distro.server.startup import load_env_file

    pid_file = pid_file_path()
    if is_running(pid_file):
        click.echo("Server is already running.", err=True)
        raise SystemExit(1)

    # Load .env files so daemon inherits env vars
    loaded = load_env_file()
    if loaded:
        click.echo(f"Loaded env: {', '.join(loaded)}")

    try:
        pid = daemonize(host=host, port=port, apps_dir=apps_dir, dev=dev)
    except RuntimeError as e:
        click.echo(f"Cannot start: {e}", err=True)
        raise SystemExit(1) from None

    click.echo(f"Server starting (PID {pid})...")

    # Wait for health
    if wait_for_health(host=host, port=port, timeout=15):
        click.echo("Server is healthy!")
        click.echo(f"  URL: http://{host}:{port}")
        click.echo(f"  PID file: {pid_file}")
    else:
        click.echo("Warning: Server started but health check not responding yet.")
        click.echo("  Check logs: ~/.amplifier/server/server.log")
        click.echo("  Crash log:  ~/.amplifier/server/crash.log")


@serve.command()
def stop() -> None:
    """Stop the running server daemon."""
    from amplifier_distro.server.daemon import pid_file_path, read_pid, stop_process

    pid_file = pid_file_path()
    pid = read_pid(pid_file)
    if pid is None:
        click.echo("No PID file found -- server may not be running.")
        return

    click.echo(f"Stopping server (PID {pid})...")
    stopped = stop_process(pid_file)
    if stopped:
        click.echo("Server stopped.")
    else:
        click.echo("Could not stop server.", err=True)
        raise SystemExit(1)


@serve.command()
@click.option(
    "--host",
    default="0.0.0.0",
    help="Bind host (use 127.0.0.1 to restrict to localhost)",
)
@click.option(
    "--port",
    default=conventions.SERVER_DEFAULT_PORT,
    type=int,
    help="Bind port",
)
@click.option("--apps-dir", default=None, help="Apps directory")
@click.option(
    "--dev",
    is_flag=True,
    help="Dev mode: mock session backend (no LLM)",
)
@click.pass_context
def restart(
    ctx: click.Context,
    host: str,
    port: int,
    apps_dir: str | None,
    dev: bool,
) -> None:
    """Restart the server daemon (stop + start)."""
    ctx.invoke(stop)
    ctx.invoke(start, host=host, port=port, apps_dir=apps_dir, dev=dev)


@serve.command("status")
def server_status() -> None:
    """Check server daemon status."""
    from amplifier_distro.server.daemon import (
        cleanup_pid,
        is_running,
        pid_file_path,
        read_pid,
    )

    pid_file = pid_file_path()
    pid = read_pid(pid_file)
    running = is_running(pid_file)

    if running and pid is not None:
        click.echo(f"Server is running (PID {pid})")
        # Check if port is responsive
        port = conventions.SERVER_DEFAULT_PORT
        if _check_port("127.0.0.1", port):
            click.echo(f"  Port {port}: listening")
            click.echo(f"  Health: http://127.0.0.1:{port}/api/health")
        else:
            click.echo(f"  Port {port}: not responding (server may be starting)")
    elif pid is not None:
        click.echo(f"Server is NOT running (stale PID file for PID {pid})")
        click.echo("  Cleaning up stale PID file...")
        cleanup_pid(pid_file)
    else:
        click.echo("Server is not running (no PID file)")


def _check_port(host: str, port: int) -> bool:
    """Check if a port is accepting connections."""
    try:
        with socket.create_connection((host, port), timeout=2):
            return True
    except (ConnectionRefusedError, TimeoutError, OSError):
        return False


# ---------------------------------------------------------------------------
# Watchdog subcommands
# ---------------------------------------------------------------------------


@serve.group("watchdog", invoke_without_command=True)
@click.pass_context
def watchdog_group(ctx: click.Context) -> None:
    """Manage the server health watchdog."""
    if ctx.invoked_subcommand is None:
        ctx.invoke(watchdog_status)


@watchdog_group.command("start")
@click.option(
    "--host",
    default="0.0.0.0",
    help="Server host to monitor",
)
@click.option(
    "--port",
    default=conventions.SERVER_DEFAULT_PORT,
    type=int,
    help="Server port to monitor",
)
@click.option(
    "--check-interval",
    default=None,
    type=int,
    help="Seconds between health checks (default: from settings or 30)",
)
@click.option(
    "--restart-after",
    default=None,
    type=int,
    help="Seconds of sustained downtime before restart (default: from settings or 300)",
)
@click.option(
    "--max-restarts",
    default=None,
    type=int,
    help="Max restart attempts, 0 = unlimited (default: from settings or 5)",
)
@click.option("--apps-dir", default=None, help="Server apps directory")
@click.option("--dev", is_flag=True, help="Server dev mode")
def watchdog_start(
    host: str,
    port: int,
    check_interval: int | None,
    restart_after: int | None,
    max_restarts: int | None,
    apps_dir: str | None,
    dev: bool,
) -> None:
    """Start the watchdog as a background process."""
    from amplifier_distro import distro_settings
    from amplifier_distro.server.watchdog import is_watchdog_running, start_watchdog

    if is_watchdog_running():
        click.echo("Watchdog is already running.", err=True)
        raise SystemExit(1)

    # CLI flags override persisted settings; settings override hardcoded defaults.
    wd = distro_settings.load().watchdog
    check_interval = check_interval if check_interval is not None else wd.check_interval
    restart_after = restart_after if restart_after is not None else wd.restart_after
    max_restarts = max_restarts if max_restarts is not None else wd.max_restarts

    pid = start_watchdog(
        host=host,
        port=port,
        check_interval=check_interval,
        restart_after=restart_after,
        max_restarts=max_restarts,
        apps_dir=apps_dir,
        dev=dev,
    )
    click.echo(f"Watchdog started (PID {pid})")
    click.echo(f"  Monitoring: http://{host}:{port}/api/health")
    click.echo(f"  Check interval: {check_interval}s")
    click.echo(f"  Restart after: {restart_after}s downtime")


@watchdog_group.command("stop")
def watchdog_stop() -> None:
    """Stop the running watchdog."""
    from amplifier_distro.server.daemon import read_pid
    from amplifier_distro.server.watchdog import stop_watchdog, watchdog_pid_file_path

    wd_pid_file = watchdog_pid_file_path()
    pid = read_pid(wd_pid_file)
    if pid is None:
        click.echo("No watchdog PID file found -- watchdog may not be running.")
        return

    click.echo(f"Stopping watchdog (PID {pid})...")
    stopped = stop_watchdog()
    if stopped:
        click.echo("Watchdog stopped.")
    else:
        click.echo("Could not stop watchdog.", err=True)
        raise SystemExit(1)


@watchdog_group.command("status")
def watchdog_status() -> None:
    """Check watchdog status."""
    from amplifier_distro.server.daemon import cleanup_pid, read_pid
    from amplifier_distro.server.watchdog import (
        is_watchdog_running,
        watchdog_pid_file_path,
    )

    wd_pid_file = watchdog_pid_file_path()
    pid = read_pid(wd_pid_file)
    running = is_watchdog_running()

    if running and pid is not None:
        click.echo(f"Watchdog is running (PID {pid})")
    elif pid is not None:
        click.echo(f"Watchdog is NOT running (stale PID file for PID {pid})")
        click.echo("  Cleaning up stale PID file...")
        cleanup_pid(wd_pid_file)
    else:
        click.echo("Watchdog is not running (no PID file)")


def _run_foreground(
    host: str,
    port: int,
    apps_dir: str | None,
    reload: bool,
    dev: bool,
    *,
    stub: bool = False,
    no_browser: bool = False,
    tls_mode: str = "off",
    ssl_certfile: str = "",
    ssl_keyfile: str = "",
    no_auth: bool = False,
) -> None:
    """Run the server in the foreground."""
    import logging

    import uvicorn

    from amplifier_distro.server.app import create_server
    from amplifier_distro.server.services import init_services
    from amplifier_distro.server.startup import (
        export_keys,
        load_env_file,
        log_startup_info,
        setup_logging,
    )

    # Set up structured logging first
    setup_logging()
    logger = logging.getLogger("amplifier_distro.server")

    # Ensure home directories exist before anything tries to use them
    from amplifier_distro import conventions

    Path(conventions.AMPLIFIER_HOME).expanduser().mkdir(parents=True, exist_ok=True)
    Path(conventions.DISTRO_HOME).expanduser().mkdir(parents=True, exist_ok=True)

    # Stub mode: activate before anything else reads AMPLIFIER_HOME
    if stub:
        from amplifier_distro.server.stub import activate_stub_mode

        stub_home = activate_stub_mode()
        click.echo("--- Stub mode: UI iteration with canned data ---")
        click.echo(f"  Temp home: {stub_home}")
        click.echo("  No real services, no writes to ~/.amplifier/")
        click.echo("  Combine with --reload for live HTML editing")
    else:
        # Load .env files (skip in stub -- stub seeds its own env)
        loaded_env = load_env_file()
        if loaded_env:
            logger.info(
                "Loaded %d var(s) from .env: %s",
                len(loaded_env),
                ", ".join(loaded_env),
            )

        # Export keys from keys.yaml
        exported = export_keys()
        if exported:
            logger.info(
                "Exported %d key(s) from keys.yaml: %s",
                len(exported),
                ", ".join(exported),
            )

    # Create session directory (before services so logs capture everything)
    from amplifier_distro.server.session_dir import (
        create_session_dir,
        setup_session_log,
    )

    session_id, session_path = create_session_dir(
        host=host,
        port=port,
        dev_mode=dev,
        stub_mode=stub,
        apps=[],  # not yet discovered; meta.json updated below
    )
    log_file = setup_session_log(session_path)
    logger.info("Session %s: %s", session_id, session_path)

    # Initialize shared services
    services = init_services(dev_mode=dev)
    click.echo(f"Services: backend={type(services.backend).__name__}")

    # Tailscale HTTPS: auto-detect and set up reverse proxy first.
    # Must happen before TLS resolution so we can skip native TLS when
    # tailscale serve is handling HTTPS as a reverse proxy.
    ts_url = _setup_tailscale(port)

    # TLS certificate resolution — skip if tailscale serve is handling HTTPS.
    # tailscale serve proxies HTTPS (port 443) → plain HTTP on our port.
    # Enabling native TLS here would break it: tailscale sends plain HTTP but
    # uvicorn would expect TLS, causing 502 Bad Gateway errors.
    ssl_kwargs: dict[str, Any] = {}
    if ts_url:
        # Tailscale serve is active as reverse proxy — native TLS not needed.
        # Always show the prominent green block so users can copy the URL to
        # their phone / other devices regardless of whether --tls was passed.
        click.echo("")
        click.echo(click.style("  ✓ HTTPS provided by Tailscale", fg="green"))
        click.echo(f"    {ts_url}")
        click.echo("")
        scheme = "https"
    else:
        # No tailscale serve — resolve certs for native TLS if requested
        from amplifier_distro.server.tls import resolve_cert

        tls_paths = resolve_cert(
            mode=tls_mode, certfile=ssl_certfile, keyfile=ssl_keyfile
        )
        if tls_paths is not None:
            ssl_kwargs["ssl_certfile"] = str(tls_paths[0])
            ssl_kwargs["ssl_keyfile"] = str(tls_paths[1])
            scheme = "https"
        else:
            scheme = "http"

    # Auth setup (conditional: TLS active + Linux + enabled).
    # TLS is considered active when either native TLS (ssl_kwargs) or
    # tailscale serve (ts_url) is providing an encrypted transport.
    from amplifier_distro.distro_settings import load as load_settings
    from amplifier_distro.server.auth import get_or_create_secret, is_auth_applicable

    settings = load_settings()
    auth_secret = ""
    tls_active = bool(ssl_kwargs) or bool(ts_url)
    if (
        not no_auth
        and tls_active
        and is_auth_applicable(
            tls_active=True,
            platform=sys.platform,
            auth_enabled=settings.server.auth.enabled,
        )
    ):
        auth_secret = get_or_create_secret()
        logger.info("PAM authentication enabled")

    server = create_server(dev_mode=dev, host=host, auth_secret=auth_secret)

    # Auto-discover apps
    loaded_apps: list[str] = []
    if apps_dir:
        discovered = server.discover_apps(Path(apps_dir))
        loaded_apps = discovered
        click.echo(f"Discovered {len(discovered)} app(s): {', '.join(discovered)}")
    else:
        # Default: discover from built-in apps directory
        builtin_apps = Path(__file__).parent / "apps"
        if builtin_apps.exists():
            discovered = server.discover_apps(builtin_apps)
            loaded_apps = discovered
            if discovered:
                click.echo(f"Loaded {len(discovered)} app(s): {', '.join(discovered)}")

    # Update meta.json with discovered apps
    _update_session_meta(session_path, {"apps": loaded_apps})

    if dev:
        click.echo("--- Dev mode: using mock session backend ---")

    # Log startup info (structured)
    log_startup_info(
        host=host,
        port=port,
        apps=loaded_apps,
        dev_mode=dev,
        logger=logger,
    )

    click.echo(f"Starting Amplifier Distro Server on {host}:{port}")
    click.echo(f"  Local: {scheme}://{host}:{port}")
    click.echo(f"  Session: {session_path}")
    click.echo(f"  Logs: {log_file}")
    if ts_url:
        click.echo(f"  HTTPS: {ts_url}  (Tailscale)")
    click.echo(f"  API docs: {scheme}://{host}:{port}/api/docs")

    if dev:
        click.echo(
            click.style(
                "  NOTE: --dev uses mock session backend (no LLM). "
                "Remove --dev for real Amplifier sessions.",
                fg="yellow",
            )
        )

    # Configure auto-browser-open (foreground, non-reload only)
    if not no_browser and not reload:
        browser_host = "127.0.0.1" if host == "0.0.0.0" else host
        server.app.state.open_browser_url = f"http://{browser_host}:{port}/loading"

    if reload:
        import os as _os

        _os.environ["_AMPLIFIER_DEV_MODE"] = "1" if dev else ""
        if apps_dir:
            _os.environ["_AMPLIFIER_APPS_DIR"] = apps_dir
        uvicorn.run(
            "amplifier_distro.server.cli:_create_app",
            host=host,
            port=port,
            reload=True,
            factory=True,
            log_level="info",
            ws_ping_interval=20,
            ws_ping_timeout=20,
            **ssl_kwargs,
        )
    else:
        uvicorn.run(
            server.app,
            host=host,
            port=port,
            log_level="info",
            ws_ping_interval=20,
            ws_ping_timeout=20,
            **ssl_kwargs,
        )


def _update_session_meta(session_path: Path, updates: dict) -> None:
    """Merge *updates* into the session's meta.json."""
    import json

    meta_file = session_path / conventions.DISTRO_SESSION_META_FILENAME
    try:
        meta = json.loads(meta_file.read_text())
        meta.update(updates)
        meta_file.write_text(json.dumps(meta, indent=2) + "\n")
    except OSError:
        pass


def _setup_tailscale(port: int) -> str | None:
    """Auto-detect Tailscale and set up HTTPS reverse proxy."""
    import atexit

    from amplifier_distro import tailscale

    url = tailscale.start_serve(port)
    if url:
        atexit.register(tailscale.stop_serve)
    return url


def _create_app():
    """Factory for uvicorn --reload mode."""
    import os

    from amplifier_distro.server.app import create_server
    from amplifier_distro.server.services import init_services
    from amplifier_distro.server.startup import export_keys, setup_logging

    dev_mode = os.environ.get("_AMPLIFIER_DEV_MODE") == "1"

    setup_logging()

    from amplifier_distro import conventions

    Path(conventions.AMPLIFIER_HOME).expanduser().mkdir(parents=True, exist_ok=True)
    Path(conventions.DISTRO_HOME).expanduser().mkdir(parents=True, exist_ok=True)

    export_keys()
    init_services(dev_mode=dev_mode)

    server = create_server(dev_mode=dev_mode)

    apps_dir = os.environ.get("_AMPLIFIER_APPS_DIR")
    if apps_dir:
        server.discover_apps(Path(apps_dir))
    else:
        builtin_apps = Path(__file__).parent / "apps"
        if builtin_apps.exists():
            server.discover_apps(builtin_apps)

    return server.app
