"""Feature catalog for the Amplifier Distro plugin.

Each feature maps to one or more bundle includes. Features are organized
into tiers. The wizard uses this catalog to generate and modify the
distro bundle.
"""

from __future__ import annotations

from dataclasses import dataclass

from distro_plugin.config import DistroPluginSettings


@dataclass(frozen=True)
class Feature:
    id: str
    name: str
    description: str
    tier: int
    includes: tuple[str, ...]
    category: str  # "memory", "planning", "search", "workflow", "content"
    requires: tuple[str, ...] = ()


FEATURES: dict[str, Feature] = {
    "dev-memory": Feature(
        id="dev-memory",
        name="Persistent Memory",
        description="Remember context, decisions, and preferences across sessions",
        tier=1,
        includes=(
            "git+https://github.com/ramparte/amplifier-collection-dev-memory@main"
            "#subdirectory=behaviors/dev-memory.yaml",
        ),
        category="memory",
    ),
    "deliberate-dev": Feature(
        id="deliberate-dev",
        name="Planning Mode",
        description="Deliberate planner, implementer, reviewer, and debugger agents",
        tier=1,
        includes=(
            "git+https://github.com/ramparte/amplifier-bundle-deliberate-development@main",
        ),
        category="planning",
    ),
    "agent-memory": Feature(
        id="agent-memory",
        name="Vector Search Memory",
        description="Semantic search across past sessions and conversations",
        tier=2,
        includes=(
            "git+https://github.com/ramparte/amplifier-bundle-agent-memory@main",
        ),
        category="search",
        requires=("dev-memory",),
    ),
    "recipes": Feature(
        id="recipes",
        name="Recipes",
        description="Multi-step workflow orchestration with approval gates",
        tier=2,
        includes=("git+https://github.com/microsoft/amplifier-bundle-recipes@main",),
        category="workflow",
    ),
    "stories": Feature(
        id="stories",
        name="Content Studio",
        description="10 specialist agents for docs, presentations, and communications",
        tier=2,
        includes=("git+https://github.com/microsoft/amplifier-bundle-stories@main",),
        category="content",
    ),
    "session-discovery": Feature(
        id="session-discovery",
        name="Session Discovery",
        description="Index and search past sessions",
        tier=2,
        includes=(
            "git+https://github.com/ramparte/amplifier-toolkit@main"
            "#subdirectory=bundles/session-discovery",
        ),
        category="search",
    ),
    "routines": Feature(
        id="routines",
        name="Routines",
        description="Scheduled AI task execution with natural language management",
        tier=2,
        includes=("git+https://github.com/microsoft/amplifier-bundle-routines@main",),
        category="workflow",
    ),
}

TIERS: dict[int, tuple[str, ...]] = {
    0: (),
    1: ("dev-memory", "deliberate-dev"),
    2: ("agent-memory", "recipes", "stories", "session-discovery", "routines"),
}


def features_for_tier(tier: int) -> list[str]:
    """Return all feature IDs that should be enabled up to a given tier."""
    result: list[str] = []
    for t in range(1, tier + 1):
        result.extend(TIERS.get(t, ()))
    return result


def get_enabled_features(settings: DistroPluginSettings) -> list[str]:
    """Return IDs of features currently included in the overlay bundle."""
    from distro_plugin.overlay import get_includes

    current_uris = set(get_includes(settings))
    return [
        fid
        for fid, feature in FEATURES.items()
        if all(inc in current_uris for inc in feature.includes)
    ]


def check_feature_uris(settings: DistroPluginSettings) -> list[str]:
    """Check enabled features for unreachable git URIs.

    Returns a list of human-readable warnings for any enabled feature
    whose bundle URI cannot be reached. This is a best-effort check
    using HTTP HEAD requests — network errors are silently ignored.
    """
    import logging
    import urllib.request

    logger = logging.getLogger(__name__)
    enabled = get_enabled_features(settings)
    warnings: list[str] = []

    for fid in enabled:
        feat = FEATURES[fid]
        for uri in feat.includes:
            # Extract the GitHub repo URL from the git+ URI
            # e.g. "git+https://github.com/org/repo@main#sub=..." -> "https://github.com/org/repo"
            repo_url = uri
            if repo_url.startswith("git+"):
                repo_url = repo_url[4:]
            repo_url = repo_url.split("@")[0].split("#")[0]

            try:
                req = urllib.request.Request(repo_url, method="HEAD")
                with urllib.request.urlopen(req, timeout=5):
                    pass  # 2xx = reachable
            except Exception:
                msg = f"Feature '{feat.name}' ({fid}): bundle may be unreachable — {repo_url}"
                warnings.append(msg)
                logger.warning(msg)

    return warnings
