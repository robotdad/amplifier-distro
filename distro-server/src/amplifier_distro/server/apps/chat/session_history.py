"""Chat session history — scans ~/.amplifier/projects/ to discover past sessions.

Schema (one dict per session in scan_sessions() output):
    session_id: str             — session directory name (UUID-like)
    cwd: str                    — working directory decoded from project dir name
    parent_session_id: str|None — parent/root session id for spawned sessions
    spawn_agent: str|None       — spawned agent name from metadata.json when present
    message_count: int          — turn_count from metadata.json (user message count)
    last_user_message: str|None — last user message text, truncated to 120 chars
    last_updated: str           — ISO-format mtime of transcript.jsonl (or session dir)
    revision: str               — mtime_ns:size signature for stale-change detection

Performance: message_count is read from metadata.json (written by
MetadataSaveHook on each orchestrator:complete).  last_user_message is
extracted by seeking to the last 8 KB of transcript.jsonl rather than
scanning every line.  Session reads are parallelised across threads.
"""

from __future__ import annotations

import json
import logging
import os
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from amplifier_distro.conventions import (
    AMPLIFIER_HOME,
    METADATA_FILENAME,
    PROJECTS_DIR,
    SESSION_INFO_FILENAME,
    TRANSCRIPT_FILENAME,
)

logger = logging.getLogger(__name__)

_AMPLIFIER_HOME_OVERRIDE: str | None = None  # Overridable in tests
# Same character set as _VALID_SESSION_ID in chat/__init__.py —
# keep in sync if session ID format changes
_VALID_SESSION_ID_RE = re.compile(r"^[a-zA-Z0-9_\-]+$")


def _get_amplifier_home() -> str:
    return (
        _AMPLIFIER_HOME_OVERRIDE
        if _AMPLIFIER_HOME_OVERRIDE is not None
        else AMPLIFIER_HOME
    )


def _decode_cwd(project_dir_name: str) -> str:
    """Decode project directory name back to filesystem path.

    The Amplifier framework encodes CWD via get_project_slug(): every '/'
    (and '\\') in the absolute path is replaced with '-'.  This is lossy —
    a literal '-' in a directory name is indistinguishable from a '/'.

    We resolve ambiguity by walking the filesystem greedily: at each
    position we try the shortest component (fewest dash-joined parts) whose
    path actually exists on disk and recurse.  If the whole path can't be
    reconstructed (temp dirs, CI, or just an unknown machine) we fall back
    to the naïve replacement.

    Example: '-Users-alice-repo-amplifier-distro'
      → filesystem finds /Users/alice/repo/amplifier-distro  ✓
      → naïve would give /Users/alice/repo/amplifier/distro  ✗
    """
    if not project_dir_name.startswith("-"):
        return project_dir_name.replace("-", "/")

    parts = project_dir_name[1:].split("-")

    def _search(idx: int, current: Path) -> str | None:
        if idx == len(parts):
            return str(current)
        # Try consuming 1…N parts as a single path component (shortest first)
        for end in range(idx + 1, len(parts) + 1):
            component = "-".join(parts[idx:end])
            candidate = current / component
            if candidate.exists():
                result = _search(end, candidate)
                if result is not None:
                    return result
        return None

    resolved = _search(0, Path("/"))
    if resolved is not None:
        return resolved
    # Fallback: naïve replacement (correct when no literal dashes exist in names)
    return "/" + "/".join(parts)


_TAIL_BYTES = 8192  # 8 KB — covers ~15-40 transcript lines from the end


def _read_last_user_message(transcript_path: Path) -> str | None:
    """Read the last user message by seeking to the tail of the transcript.

    Reads only the final ``_TAIL_BYTES`` bytes of the file and scans
    backwards for the most recent ``role: "user"`` entry.  Returns
    ``None`` if no user message is found in the tail or on any I/O error.
    """
    try:
        size = transcript_path.stat().st_size
        if size == 0:
            return None
        with transcript_path.open("rb") as f:
            f.seek(max(0, size - _TAIL_BYTES))
            chunk = f.read().decode("utf-8", errors="replace")
        for line in reversed(chunk.splitlines()):
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(entry, dict) or entry.get("role") != "user":
                continue
            content = entry.get("content", "")
            if isinstance(content, str):
                return content[:120]
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        return (block.get("text") or "")[:120]
        return None
    except OSError:
        return None


def _read_session_meta(session_dir: Path) -> dict[str, Any]:
    """Extract lightweight metadata from a single session directory."""
    # Try to read CWD from session-info.json (written by Amplifier framework)
    cwd_from_info: str | None = None
    info_path = session_dir / SESSION_INFO_FILENAME
    try:
        data = json.loads(info_path.read_text(encoding="utf-8"))
        raw = data.get("working_dir")
        if isinstance(raw, str) and raw:
            normalized = os.path.normpath(raw)
            if os.path.isabs(normalized) and len(normalized) <= 4096:
                cwd_from_info = normalized
            else:
                cwd_from_info = None
        else:
            cwd_from_info = None
    except (OSError, json.JSONDecodeError):
        pass

    parent_session_id: str | None = None
    spawn_agent: str | None = None
    session_name: str | None = None
    session_description: str | None = None
    message_count = 0
    metadata_path = session_dir / METADATA_FILENAME
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        if isinstance(metadata, dict):
            raw_parent = metadata.get("parent_id")
            if (
                isinstance(raw_parent, str)
                and raw_parent
                and _VALID_SESSION_ID_RE.fullmatch(raw_parent)
            ):
                parent_session_id = raw_parent
            raw_agent = metadata.get("agent_name")
            if isinstance(raw_agent, str) and raw_agent:
                spawn_agent = raw_agent
            raw_name = metadata.get("name")
            if isinstance(raw_name, str) and raw_name:
                session_name = raw_name
            raw_description = metadata.get("description")
            if isinstance(raw_description, str) and raw_description:
                session_description = raw_description
            # turn_count is written by MetadataSaveHook on each
            # orchestrator:complete — avoids scanning the transcript.
            raw_turn_count = metadata.get("turn_count")
            if isinstance(raw_turn_count, int) and raw_turn_count >= 0:
                message_count = raw_turn_count
    except (OSError, json.JSONDecodeError):
        pass

    transcript_path = session_dir / TRANSCRIPT_FILENAME
    last_user_message: str | None = None

    last_updated, revision = _session_revision_signature(session_dir)

    if transcript_path.exists():
        last_user_message = _read_last_user_message(transcript_path)
        # Fallback for old sessions without turn_count in metadata:
        # if transcript is non-empty, ensure message_count >= 1 so the
        # session passes the frontend's empty-session filter.
        if message_count == 0:
            try:
                if transcript_path.stat().st_size > 0:
                    message_count = 1
            except OSError:
                pass

    return {
        "session_id": session_dir.name,
        "message_count": message_count,
        "last_user_message": last_user_message,
        "last_updated": last_updated,
        "revision": revision,
        "cwd_from_info": cwd_from_info,  # verbatim CWD if available
        "parent_session_id": parent_session_id,
        "spawn_agent": spawn_agent,
        "name": session_name,
        "description": session_description,
    }


def _session_revision_signature(session_dir: Path) -> tuple[str, str]:
    """Return (last_updated_iso, revision_signature) for one session directory."""
    transcript_path = session_dir / TRANSCRIPT_FILENAME
    stat_target = transcript_path if transcript_path.exists() else session_dir
    try:
        stat = stat_target.stat()
        last_updated = datetime.fromtimestamp(stat.st_mtime, tz=UTC).isoformat()
        mtime_ns = getattr(stat, "st_mtime_ns", int(stat.st_mtime * 1_000_000_000))
        revision = f"{int(mtime_ns)}:{int(stat.st_size)}"
        return last_updated, revision
    except OSError:
        return datetime.now(tz=UTC).isoformat(), "0:0"


def _iter_session_dirs(projects_path: Path) -> list[Path]:
    """Return validated session directories under ~/.amplifier/projects."""
    if not projects_path.exists():
        return []

    try:
        project_dirs = list(projects_path.iterdir())
    except OSError:
        logger.warning("Could not list projects at %s", projects_path, exc_info=True)
        return []

    try:
        resolved_projects = projects_path.resolve()
    except OSError:
        logger.warning(
            "Could not resolve projects path %s",
            projects_path,
            exc_info=True,
        )
        return []

    session_dirs: list[Path] = []
    for project_dir in project_dirs:
        # Symlink containment — skip any project dir that escapes projects_path
        try:
            if project_dir.resolve().parent != resolved_projects:
                logger.warning("Skipping symlink escape: %s", project_dir)
                continue
        except OSError:
            logger.warning(
                "Could not resolve path for %s — skipping", project_dir, exc_info=True
            )
            continue

        if not project_dir.is_dir():
            continue

        sessions_subdir = project_dir / "sessions"
        if not sessions_subdir.is_dir():
            continue

        try:
            resolved_sessions = sessions_subdir.resolve()
            candidates = [
                d
                for d in sessions_subdir.iterdir()
                if d.is_dir() and d.resolve().is_relative_to(resolved_sessions)
            ]
        except OSError:
            logger.warning(
                "Could not list sessions in %s", sessions_subdir, exc_info=True
            )
            continue

        for session_dir in candidates:
            if not _VALID_SESSION_ID_RE.fullmatch(session_dir.name):
                logger.debug(
                    "Skipping session dir with non-standard name: %r", session_dir.name
                )
                continue
            session_dirs.append(session_dir)

    return session_dirs


_SCAN_WORKERS = 8  # Thread pool size for parallel session reads (I/O-bound)


def _stat_mtime(session_dir: Path) -> float:
    """Return mtime of transcript (or session dir) for sorting.  0.0 on error."""
    transcript = session_dir / TRANSCRIPT_FILENAME
    target = transcript if transcript.exists() else session_dir
    try:
        return target.stat().st_mtime
    except OSError:
        return 0.0


def scan_sessions(
    amplifier_home: str | None = None,
    *,
    limit: int = 0,
    pinned_ids: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Scan ~/.amplifier/projects/ and return lightweight metadata for all sessions.

    Returns a list sorted newest-first by last_updated.
    Never raises — malformed sessions are included with degraded metadata.

    When *limit* > 0, only the newest *limit* session directories (plus any
    whose session_id appears in *pinned_ids*) are fully read.  The remaining
    directories are skipped, avoiding expensive metadata/transcript I/O for
    sessions that would be sliced away by the caller anyway.

    Session directories are read in parallel using a thread pool since each
    read is I/O-bound (stat + small JSON file reads + transcript tail seek).
    """
    home = amplifier_home or _get_amplifier_home()
    projects_path = Path(home).expanduser() / PROJECTS_DIR
    session_dirs = _iter_session_dirs(projects_path)

    if not session_dirs:
        return []

    # When a limit is set, stat all dirs for mtime (very cheap — ~0.03s for
    # 5000 dirs) then only fully read the top N + any pinned sessions.
    if limit > 0:
        pinned = pinned_ids or set()
        scored = [(sd, _stat_mtime(sd)) for sd in session_dirs]
        scored.sort(key=lambda x: x[1], reverse=True)

        selected: list[Path] = []
        selected_ids: set[str] = set()
        for sd, _mtime in scored:
            if len(selected) < limit or sd.name in pinned:
                selected.append(sd)
                selected_ids.add(sd.name)

        # Include any pinned sessions that fell outside the top N
        for sd, _mtime in scored:
            if sd.name in pinned and sd.name not in selected_ids:
                selected.append(sd)
                selected_ids.add(sd.name)

        session_dirs = selected

    def _process(session_dir: Path) -> dict[str, Any] | None:
        try:
            meta = _read_session_meta(session_dir)
            project_dir_name = session_dir.parent.parent.name
            # Prefer verbatim CWD from session-info.json; fall back to decoded name
            meta["cwd"] = meta.pop("cwd_from_info") or _decode_cwd(project_dir_name)
            return meta
        except Exception:  # noqa: BLE001
            logger.warning(
                "Skipping session %s due to unexpected error",
                session_dir,
                exc_info=True,
            )
            return None

    with ThreadPoolExecutor(max_workers=_SCAN_WORKERS) as pool:
        results = [r for r in pool.map(_process, session_dirs) if r is not None]

    results.sort(key=lambda s: s["last_updated"], reverse=True)
    return results


def scan_session_revisions(
    session_ids: set[str] | None = None,
    amplifier_home: str | None = None,
) -> list[dict[str, Any]]:
    """Return lightweight revision metadata for session directories on disk.

    Includes name/description from metadata.json so the frontend can update
    session titles without a full history fetch.
    """
    home = amplifier_home or _get_amplifier_home()
    projects_path = Path(home).expanduser() / PROJECTS_DIR
    wanted = set(session_ids) if session_ids is not None else None

    rows: list[dict[str, Any]] = []
    for session_dir in _iter_session_dirs(projects_path):
        session_id = session_dir.name
        if wanted is not None and session_id not in wanted:
            continue
        last_updated, revision = _session_revision_signature(session_dir)
        row: dict[str, Any] = {
            "session_id": session_id,
            "last_updated": last_updated,
            "revision": revision,
        }
        # Read name/description from metadata.json for live title updates
        metadata_path = session_dir / METADATA_FILENAME
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            if isinstance(metadata, dict):
                if isinstance(metadata.get("name"), str) and metadata["name"]:
                    row["name"] = metadata["name"]
                if (
                    isinstance(metadata.get("description"), str)
                    and metadata["description"]
                ):
                    row["description"] = metadata["description"]
        except (OSError, json.JSONDecodeError):
            pass
        rows.append(row)

    rows.sort(key=lambda s: s["last_updated"], reverse=True)
    return rows
