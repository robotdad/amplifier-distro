"""Bundle preflight validation for distro-server.

Provides two validation levels:

- **Lightweight**: YAML syntax and structure check (no downloads, no network).
- **Full**: Load bundle, compose includes, prepare providers (downloads if needed).

A global ``asyncio.Lock`` prevents concurrent overlay mutations from
racing with preflight checks.

Public API:

- ``run_startup_preflight()`` -- FastAPI startup event handler.
- ``safe_overlay_mutation()`` -- async context manager for endpoints
  that modify the overlay.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from collections.abc import AsyncIterator
from pathlib import Path

import yaml

from amplifier_distro.overlay import (
    migrate_overlay,
    overlay_bundle_path,
    overlay_dir,
    restore_overlay,
    snapshot_overlay,
)

logger = logging.getLogger(__name__)

# Prevents concurrent overlay mutations and preflight runs.
OVERLAY_LOCK = asyncio.Lock()

# When True (default), startup preflight failure crashes the server (fail-fast).
# Set AMPLIFIER_PREFLIGHT_STRICT=0 to downgrade to a warning.
_strict_env = os.environ.get("AMPLIFIER_PREFLIGHT_STRICT", "1").lower()
STARTUP_PREFLIGHT_STRICT = _strict_env not in ("0", "false")


# -------------------------------------------------------------------
# Validation helpers
# -------------------------------------------------------------------


def preflight_lightweight(path: Path) -> None:
    """Validate overlay YAML syntax and basic bundle structure.

    No network I/O, no downloads -- just parse and check shape.
    Raises ``ValueError`` on any structural issue.  Silently returns
    when the overlay file does not exist yet.
    """
    if not path.exists():
        return

    text = path.read_text()
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ValueError(f"Invalid YAML in overlay: {exc}") from exc

    if not isinstance(data, dict):
        raise ValueError(f"Overlay must be a YAML mapping, got {type(data).__name__}")

    if "bundle" not in data:
        raise ValueError("Overlay missing required 'bundle' key")

    bundle_meta = data["bundle"]
    if not isinstance(bundle_meta, dict) or "name" not in bundle_meta:
        raise ValueError("Overlay 'bundle' must be a mapping with at least a 'name'")

    includes = data.get("includes", [])
    if not isinstance(includes, list):
        raise ValueError("'includes' must be a list")
    for i, entry in enumerate(includes):
        if isinstance(entry, dict):
            if "bundle" not in entry:
                raise ValueError(f"includes[{i}] is a mapping but missing 'bundle' key")
        elif not isinstance(entry, str):
            raise ValueError(
                f"includes[{i}] must be a string or mapping, got {type(entry).__name__}"
            )


async def preflight_full(dir_path: Path) -> None:
    """Full preflight: load -> compose -> prepare.

    Downloads modules if not already cached by foundation.  Validates
    that the bundle can be fully prepared for session creation.
    Raises on any failure.
    """
    from amplifier_foundation import load_bundle

    logger.info("Running full bundle preflight from %s", dir_path)
    bundle = await load_bundle(str(dir_path), strict=True)
    await bundle.prepare()
    logger.info("Full bundle preflight passed")


# -------------------------------------------------------------------
# Startup preflight
# -------------------------------------------------------------------


async def run_startup_preflight() -> None:
    """Run full preflight at server startup if system is configured.

    Registered as a FastAPI ``startup`` event handler.  If the system
    is not yet configured (no overlay or no provider key), this is a
    no-op.

    Behaviour on failure is controlled by ``STARTUP_PREFLIGHT_STRICT``:

    - **strict** (default): exception propagates, causing uvicorn to
      exit (fail-fast).
    - **warn** (``AMPLIFIER_PREFLIGHT_STRICT=0``): logs a warning and
      allows the server to start.  Bundle issues will surface later at
      session-creation time.
    """
    # Lazy import to avoid circular dependency (settings -> preflight).
    from amplifier_distro.server.apps.settings import compute_phase

    phase = compute_phase()
    if phase != "ready":
        logger.info("Startup preflight skipped (phase=%s)", phase)
        return

    try:
        async with OVERLAY_LOCK:
            logger.info("Running startup bundle preflight...")
            migrate_overlay()
            preflight_lightweight(overlay_bundle_path())

            # Ensure provider SDK dependencies are installed before the
            # full preflight (which will try to import them).  This
            # recovers automatically after venv wipes from reinstalls.
            from amplifier_distro.features import ensure_configured_provider_modules

            installed = ensure_configured_provider_modules()
            if installed:
                logger.info(
                    "Installed provider modules at startup: %s",
                    ", ".join(installed),
                )

            await preflight_full(overlay_dir())
            logger.info("Startup bundle preflight passed")
    except Exception:
        if STARTUP_PREFLIGHT_STRICT:
            raise
        logger.warning(
            "Startup preflight failed (non-fatal because AMPLIFIER_PREFLIGHT_STRICT=0)",
            exc_info=True,
        )


# -------------------------------------------------------------------
# Post-mutation preflight
# -------------------------------------------------------------------


async def _run_mutation_preflight() -> None:
    """Run validation after an overlay mutation.

    Always runs the lightweight check.  Runs the full check only when
    the system is already configured (overlay exists + provider key
    present).  Must be called with ``OVERLAY_LOCK`` held.
    """
    from amplifier_distro.server.apps.settings import compute_phase

    preflight_lightweight(overlay_bundle_path())
    if compute_phase() == "ready":
        await preflight_full(overlay_dir())


@contextlib.asynccontextmanager
async def safe_overlay_mutation() -> AsyncIterator[None]:
    """Context manager for overlay mutations with validation and rollback.

    Acquires the overlay lock, snapshots the current ``bundle.yaml``,
    yields for the caller to perform mutations, then runs preflight
    validation.  On **any** exception (mutation error *or* preflight
    failure) the snapshot is restored and the exception re-raised.

    Usage::

        async with safe_overlay_mutation():
            overlay.add_include(some_uri)
            overlay.remove_include(other_uri)
    """
    async with OVERLAY_LOCK:
        snap = snapshot_overlay()
        try:
            yield
            await _run_mutation_preflight()
        except Exception:
            restore_overlay(snap)
            raise
