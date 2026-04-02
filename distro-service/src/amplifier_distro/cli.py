"""Smart defaults based on --host: amp-distro (default): localhost mode (127.0.0.1, TLS off, no auth); amp-distro --host 0.0.0.0: network mode (TLS auto, auth enabled).

Additional commands: backup, restore, doctor, service
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import click

from . import conventions


def _is_non_localhost(host: str) -> bool:
    """Return True if host is not a localhost address."""
    return host not in ("127.0.0.1", "localhost", "::1")


def _serve_options(func):
    """Shared CLI options for starting the experience server."""
    options = [
        click.option(
            "--host",
            default=None,
            help="Bind host address. Use 0.0.0.0 for network access (enables TLS+auth).",
        ),
        click.option("--port", default=None, type=int, help="Bind port number."),
        click.option(
            "--tls",
            "tls_mode",
            default=None,
            type=click.Choice(["auto", "off", "manual"], case_sensitive=False),
            help="TLS mode.",
        ),
        click.option(
            "--ssl-certfile",
            default=None,
            help="Path to SSL certificate file (implies --tls manual).",
        ),
        click.option(
            "--ssl-keyfile",
            default=None,
            help="Path to SSL private key file (used with --ssl-certfile).",
        ),
        click.option(
            "--no-auth", is_flag=True, default=False, help="Disable authentication."
        ),
        click.option(
            "--reload",
            is_flag=True,
            default=False,
            help="Enable hot-reload for development.",
        ),
        click.option(
            "--log-level", default=None, help="Log level: debug|info|warning|error."
        ),
        click.option(
            "--no-browser",
            is_flag=True,
            default=False,
            help="Do not open a browser tab when the server becomes ready.",
        ),
    ]
    for option in reversed(options):
        func = option(func)
    return func


@click.group(
    invoke_without_command=True,
    help="amp-distro — Amplifier distro experience service.",
)
@_serve_options
@click.pass_context
def main(
    ctx: click.Context,
    host: str | None,
    port: int | None,
    tls_mode: str | None,
    ssl_certfile: str | None,
    ssl_keyfile: str | None,
    no_auth: bool,
    reload: bool,
    log_level: str | None,
    no_browser: bool,
) -> None:
    """amp-distro — Amplifier distro experience service."""
    if ctx.invoked_subcommand is None:
        # Resolve effective host defaulting to localhost
        effective_host = host or "127.0.0.1"

        # Apply smart defaults based on host
        if _is_non_localhost(effective_host):
            effective_tls = tls_mode or "auto"
            auth_by_default = not no_auth
            if effective_tls == "off":
                click.echo(
                    "Warning: running on network interface without TLS.", err=True
                )
        else:
            effective_tls = tls_mode or "off"
            auth_by_default = False

        ctx.invoke(
            _start_server,
            host=effective_host,
            port=port,
            tls_mode=effective_tls,
            ssl_certfile=ssl_certfile,
            ssl_keyfile=ssl_keyfile,
            no_auth=no_auth,
            reload=reload,
            log_level=log_level,
            home_redirect="/distro/",
            auth_by_default=auth_by_default,
            no_browser=no_browser,
        )


def _detect_wsl() -> bool:
    """Return True if running inside Windows Subsystem for Linux."""
    try:
        with open("/proc/version") as fh:
            return "microsoft" in fh.read().lower()
    except OSError:
        return False


def _open_browser(url: str) -> None:
    """Open *url* in the user's browser — macOS, Linux, or WSL-aware.

    Swallows all errors silently; browser launch is best-effort.
    """
    import subprocess

    if _detect_wsl():
        # WSL: hand off to the Windows browser via PowerShell
        try:
            subprocess.run(
                ["powershell.exe", "start", url],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except FileNotFoundError:
            pass  # powershell.exe not on PATH — skip silently
        return

    import sys

    if sys.platform == "darwin":
        subprocess.run(
            ["open", url],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    else:
        # Linux (non-WSL): xdg-open is the standard launcher
        subprocess.run(
            ["xdg-open", url],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )


def _wait_and_announce(
    url: str,
    ready_url: str,
    *,
    open_browser: bool,
    poll_interval: float = 2.0,
    timeout: float = 300.0,
) -> None:
    """Background thread: open browser when server accepts connections, announce when ready.

    Two-phase approach:
      Phase 1 — Wait for the server to start accepting HTTP requests (loading
                 page is live).  Opens the browser at this point so the user
                 sees the loading screen while pre-warm continues.
      Phase 2 — Continue polling until /ready returns {"ready": true}, then
                 re-print the URL prominently so it is visible in the log.

    Runs as a daemon thread — never raises; swallows all exceptions silently.
    """
    import ssl
    import time
    import urllib.request

    # Accept self-signed certs for --tls auto / --tls manual
    ssl_ctx = ssl.create_default_context()
    ssl_ctx.check_hostname = False
    ssl_ctx.verify_mode = ssl.CERT_NONE

    deadline = time.monotonic() + timeout

    # --- Phase 1: wait for the server to respond at all ---
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(ready_url, timeout=2, context=ssl_ctx) as resp:
                data = json.loads(resp.read())
            # Got a response — server is up and serving the loading page.
            if open_browser:
                _open_browser(url)
            # If it's already fully ready on first poll, announce and exit.
            if data.get("ready"):
                click.echo(f"\n  \u2713 amp-distro ready \u2014 {url}\n")
                return
            break  # server is up but still warming — move to phase 2
        except Exception:
            pass
        time.sleep(poll_interval)
    else:
        # Timed out before server accepted any connection — surface URL and bail.
        click.echo(f"\n  amp-distro: {url}\n")
        return

    # --- Phase 2: wait for pre-warm to finish, then re-announce the URL ---
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(ready_url, timeout=2, context=ssl_ctx) as resp:
                data = json.loads(resp.read())
            if data.get("ready"):
                click.echo(f"\n  \u2713 amp-distro ready \u2014 {url}\n")
                return
        except Exception:
            pass
        time.sleep(poll_interval)

    # Timed out during warm-up — surface the URL anyway so it is not lost.
    click.echo(f"\n  amp-distro: {url}\n")


def _start_server(
    host: str,
    port: int | None,
    tls_mode: str,
    ssl_certfile: str | None,
    ssl_keyfile: str | None,
    no_auth: bool,
    reload: bool,
    log_level: str | None,
    home_redirect: str | None,
    *,
    auth_by_default: bool = False,
    no_browser: bool = False,
) -> None:
    """Common server startup logic."""
    # --ssl-certfile implies manual TLS mode when tls_mode is still default
    if tls_mode == "off" and ssl_certfile:
        tls_mode = "manual"
    if tls_mode == "auto" and ssl_certfile:
        tls_mode = "manual"

    # Set distro-specific env vars
    if home_redirect:
        os.environ.setdefault("AMPLIFIERD_HOME_REDIRECT", home_redirect)

    os.environ.setdefault("AMPLIFIERD_TLS_MODE", tls_mode)

    if no_auth:
        os.environ["AMPLIFIERD_AUTH_ENABLED"] = "false"
    elif auth_by_default:
        os.environ.setdefault("AMPLIFIERD_AUTH_ENABLED", "true")

    # ssl_certfile / ssl_keyfile: amplifierd doesn't accept these as CLI params yet.
    # Stash them in env vars so future amplifierd versions can pick them up via
    # DaemonSettings (AMPLIFIERD_SSL_CERTFILE / AMPLIFIERD_SSL_KEYFILE).
    if ssl_certfile:
        os.environ.setdefault("AMPLIFIERD_SSL_CERTFILE", ssl_certfile)
    if ssl_keyfile:
        os.environ.setdefault("AMPLIFIERD_SSL_KEYFILE", ssl_keyfile)

    # Pre-flight port check — fail early with a clear message instead of
    # letting amplifierd silently fall back to a different port.
    from .server.daemon import check_port, remove_pid, write_pid

    effective_port = port if port is not None else conventions.SERVER_DEFAULT_PORT
    if not check_port(host, effective_port):
        click.echo(
            f"\nError: Port {effective_port} is already in use.\n"
            f"A previous amp-distro instance may still be shutting down.\n"
            f"Wait a moment and try again, or use --port to specify a different port.\n",
            err=True,
        )
        raise SystemExit(1)

    # Pre-startup summary — gives the user something to see while bundles load.
    scheme = "https" if tls_mode != "off" else "http"
    base_url = f"{scheme}://{host}:{effective_port}"
    click.echo(
        f"\n  amp-distro starting on {base_url}\n"
        f"  Bundle loading may take a minute on first run.\n"
    )

    # Background thread: re-announces the URL and optionally opens the browser
    # once the /ready endpoint reports the server is fully warmed up.
    import threading

    _ready_thread = threading.Thread(
        target=_wait_and_announce,
        args=(base_url, f"{base_url}/ready"),
        kwargs={"open_browser": not no_browser},
        daemon=True,
        name="amp-distro-ready-watcher",
    )
    _ready_thread.start()

    # Write PID file so doctor and other tools can find the running server.
    pid_path = (
        Path(conventions.AMPLIFIER_HOME).expanduser()
        / conventions.SERVER_DIR
        / conventions.SERVER_PID_FILE
    )
    write_pid(pid_path)

    # Delegate to amplifierd's serve command.
    # Only pass params that amplifierd's serve() click command actually declares.
    # TLS mode, auth, and SSL paths are communicated via AMPLIFIERD_* env vars above.
    from amplifierd.cli import serve as amplifierd_serve

    ctx = click.get_current_context()
    try:
        ctx.invoke(
            amplifierd_serve,
            host=host,
            port=port,
            reload=reload,
            log_level=log_level,
            bundle=(),
            default_bundle=None,
        )
    finally:
        remove_pid(pid_path)

    # Post-shutdown message — confirms clean exit.
    click.echo(f"\n  amp-distro stopped ({host}:{effective_port}).\n")


# -- Backup commands -----------------------------------------------------


@main.command("backup", help="Back up Amplifier state to a private GitHub repo.")
@click.option("--name", default="amplifier-backup", help="Backup repo name.")
def backup_cmd(name: str) -> None:
    """Back up Amplifier state to a private GitHub repo."""
    from .backup import _detect_gh_handle, backup

    gh_handle = _detect_gh_handle()
    if not gh_handle:
        click.echo(
            "Error: Could not detect GitHub handle. "
            "Is the gh CLI installed and authenticated?",
            err=True,
        )
        sys.exit(1)

    amplifier_home = Path(conventions.AMPLIFIER_HOME).expanduser()
    click.echo("Starting backup...")
    result = backup(amplifier_home, gh_handle, repo_name=name)

    if result.status == "success":
        click.echo(f"  {result.message}")
        for f in result.files:
            click.echo(f"    {f}")
    else:
        click.echo(f"Backup failed: {result.message}", err=True)
        sys.exit(1)


@main.command("restore", help="Restore Amplifier state from a private GitHub repo.")
@click.option("--name", default="amplifier-backup", help="Backup repo name.")
def restore_cmd(name: str) -> None:
    """Restore Amplifier state from a private GitHub repo."""
    from .backup import _detect_gh_handle, restore

    gh_handle = _detect_gh_handle()
    if not gh_handle:
        click.echo(
            "Error: Could not detect GitHub handle. "
            "Is the gh CLI installed and authenticated?",
            err=True,
        )
        sys.exit(1)

    amplifier_home = Path(conventions.AMPLIFIER_HOME).expanduser()
    click.echo("Starting restore...")
    result = restore(amplifier_home, gh_handle, repo_name=name)

    if result.status == "success":
        click.echo(f"  {result.message}")
        for f in result.files:
            click.echo(f"    {f}")
    else:
        click.echo(f"Restore failed: {result.message}", err=True)
        sys.exit(1)


# -- Doctor command ------------------------------------------------------


@main.command("doctor")
@click.option("--fix", is_flag=True, help="Auto-fix issues that can be resolved.")
@click.option("--json", "as_json", is_flag=True, help="Output machine-readable JSON.")
def doctor_cmd(fix: bool, as_json: bool) -> None:
    """Diagnose and auto-fix common problems.

    Runs a comprehensive suite of checks against the local Amplifier
    installation.  Use --fix to automatically resolve fixable issues
    (missing directories, wrong permissions, stale PID files).
    """
    from .doctor import run_diagnostics, run_fixes

    amplifier_home = Path(conventions.AMPLIFIER_HOME).expanduser()
    report = run_diagnostics(amplifier_home)

    # Apply fixes if requested
    fixes_applied: list[str] = []
    if fix:
        fixes_applied = run_fixes(amplifier_home, report)
        # Re-run diagnostics to show updated state
        if fixes_applied:
            report = run_diagnostics(amplifier_home)

    if as_json:
        _print_doctor_json(report, fixes_applied)
    else:
        _print_doctor_report(report, fixes_applied)

    # Exit non-zero if any errors remain
    if report.summary["error"] > 0:
        sys.exit(1)


def _print_doctor_report(report: object, fixes: list[str]) -> None:
    """Format and print a doctor report with coloured status markers."""
    click.echo("Amplifier Distro - Doctor\n")

    for check in report.checks:  # type: ignore[union-attr]
        if check.status == "ok":
            mark = click.style("\u2714", fg="green")  # checkmark
        elif check.status == "warning":
            mark = click.style("!", fg="yellow")
        else:
            mark = click.style("\u2718", fg="red")  # X

        click.echo(f"  {mark} {check.name}: {check.message}")

        # Show fix suggestion for non-ok checks that have a fix
        if check.status != "ok" and check.fix_available:
            click.echo(click.style(f"    fix: {check.fix_description}", fg="cyan"))

    # Summary
    s = report.summary  # type: ignore[union-attr]
    click.echo(f"\n  {s['ok']} ok, {s['warning']} warning(s), {s['error']} error(s)")

    if fixes:
        click.echo("\nFixes applied:")
        checkmark = click.style("\u2714", fg="green")
        for f in fixes:
            click.echo(f"  {checkmark} {f}")


def _print_doctor_json(report: object, fixes: list[str]) -> None:
    """Print the doctor report as machine-readable JSON."""
    data = {
        "checks": [c.model_dump() for c in report.checks],  # type: ignore[union-attr]
        "summary": report.summary,  # type: ignore[union-attr]
        "fixes_applied": fixes,
    }
    click.echo(json.dumps(data, indent=2))


# -- Service commands ----------------------------------------------------


@main.group("service")
def service_group() -> None:
    """Manage platform auto-start service (systemd/launchd)."""


@service_group.command("install")
@click.option(
    "--no-watchdog",
    is_flag=True,
    help="Install server only, without the health watchdog.",
)
@click.option(
    "--host",
    default="127.0.0.1",
    help="Bind host (use 0.0.0.0 for network access).",
)
@click.option(
    "--port",
    default=None,
    type=int,
    help="Bind port (default: 8410).",
)
@click.option(
    "--tls",
    "tls_mode",
    default=None,
    type=click.Choice(["auto", "off", "manual"], case_sensitive=False),
    help="TLS mode for the generated service unit. Omit to use smart defaults at runtime.",
)
def service_install(
    no_watchdog: bool, host: str, port: int | None, tls_mode: str | None
) -> None:
    """Install the platform service for auto-start on boot."""
    from .service import install_service

    if port is None:
        port = conventions.SERVER_DEFAULT_PORT
    result = install_service(
        include_watchdog=not no_watchdog, host=host, port=port, tls_mode=tls_mode
    )
    if result.success:
        click.echo(f"Service installed ({result.platform})")
        for detail in result.details:
            click.echo(f"  {detail}")
    else:
        click.echo(f"Failed: {result.message}", err=True)
        for detail in result.details:
            click.echo(f"  {detail}", err=True)
        raise SystemExit(1)


@service_group.command("uninstall")
def service_uninstall() -> None:
    """Remove the platform auto-start service."""
    from .service import uninstall_service

    result = uninstall_service()
    if result.success:
        click.echo(f"Service removed ({result.platform})")
        for detail in result.details:
            click.echo(f"  {detail}")
    else:
        click.echo(f"Failed: {result.message}", err=True)
        raise SystemExit(1)


@service_group.command("status")
def service_cmd_status() -> None:
    """Check platform service status."""
    from .service import service_status

    result = service_status()
    click.echo(f"Platform: {result.platform}")
    click.echo(f"Status: {result.message}")
    for detail in result.details:
        click.echo(f"  {detail}")
