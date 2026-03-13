"""Zero-arg distro settings loader for distro-service convenience.

Wraps ``distro_plugin.distro_settings.load()`` so callers inside
``amplifier_distro`` (e.g. ``doctor.py``) can call ``load_settings()``
without constructing a ``DistroPluginSettings`` instance each time.
"""

from __future__ import annotations

from distro_plugin.config import DistroPluginSettings
from distro_plugin.distro_settings import DistroSettings as DistroSettings
from distro_plugin.distro_settings import load as _load


def load() -> DistroSettings:
    """Load distro settings using default DistroPluginSettings."""
    return _load(DistroPluginSettings())
