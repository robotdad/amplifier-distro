#!/bin/bash
# Amplifier Distro - Install Script
#
# Single script used by all install paths:
#
#   Codespaces / devcontainer:
#     postCreateCommand: bash scripts/install.sh
#
#   Dockerfile:
#     RUN bash scripts/install.sh
#
#   curl | bash (standalone):
#     curl -fsSL https://raw.githubusercontent.com/ramparte/amplifier-distro/main/scripts/install.sh | bash
#
#   Local developer:
#     git clone ... && cd amplifier-distro && bash scripts/install.sh
#
# Behavior:
#   - If pyproject.toml exists in cwd → editable install (developer / devcontainer)
#   - Otherwise → clone repo and install as uv tools (standalone)

set -e

REPO_URL="https://github.com/microsoft/amplifier-distro"

# ── Ensure git is available ─────────────────────────────────────
ensure_git() {
    if command -v git &>/dev/null; then
        return
    fi
    echo "[install] ERROR: git is required but not installed."
    echo "  Install git and try again: https://git-scm.com/downloads"
    exit 1
}

# ── Ensure gh CLI is available ─────────────────────────────────
ensure_gh() {
    if command -v gh &>/dev/null; then
        return
    fi
    echo "[install] ERROR: GitHub CLI (gh) is required but not installed."
    echo "  Install it and try again: https://cli.github.com"
    exit 1
}

# ── Ensure uv is available ───────────────────────────────────────
ensure_uv() {
    if command -v uv &>/dev/null; then
        echo "[install] uv: $(uv --version)"
        return
    fi
    echo "[install] Installing uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$PATH"
}

# ── Main ─────────────────────────────────────────────────────────
echo "=== Amplifier Distro - Install ==="
echo ""

ensure_git
ensure_gh
ensure_uv
echo ""

echo "[1/3] Installing amplifier-distro..."
uv tool install --force "git+${REPO_URL}@main#subdirectory=distro-server"

echo ""
echo "=== Install complete ==="
echo ""
echo "To get started:"
echo "  run 'amp-distro serve'"
echo "  Browse to http://localhost:8400"
