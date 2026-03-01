# Chat-to-Voice Session Handoff Implementation Plan

> **Execution:** Use the `subagent-driven-development` workflow to implement this plan.
> **Design doc:** `docs/plans/2026-03-01-chat-to-voice-design.md`
> **Worktree:** `worktrees/chat-to-voice/`

**Goal:** Enable the voice app to resume any Amplifier session (chat, CLI, Slack) by injecting prior text context into the OpenAI Realtime session at the moment of resumption.

**Architecture:** A new `context.py` module in the `transcript/` package provides pure-Python helpers that read Foundation session files and convert them to Realtime API items. A dispatcher branches on `created_by_app` — voice sessions take the existing path unchanged; everything else goes through the new chat context builder. The `list_sessions` route is widened to merge the voice index with a filesystem scan of `~/.amplifier/projects/`.

**Tech Stack:** Python 3.11, FastAPI, pytest, pathlib, json — no new dependencies.

---

## Overview

Five phases, nine tasks. Tasks 1–4 are pure unit-test work on a new file (no route changes yet). Tasks 5–6 wire into the routes. Tasks 7–8 widen the session list and update the frontend. Task 9 is the quality/regression gate.

All work happens inside `distro-server/`. Test commands are run from that directory.

```
distro-server/
  src/amplifier_distro/server/apps/voice/
    __init__.py          ← modified: Tasks 5, 6, 7
    static/index.html    ← modified: Task 8
    transcript/
      context.py         ← CREATED: Tasks 1–4
      repository.py      ← modified: Task 5 (add created_by_app to metadata write)
  tests/apps/voice/
    test_voice_context.py   ← CREATED: Tasks 1–4
    test_voice_routes.py    ← modified: Tasks 5, 6, 7
    test_voice_static_index.py ← modified: Task 8
```

---

## Tasks

---

### Task 1: Create `context.py` with `_extract_text_turns()`

Extract plain `{role, text}` pairs from an Amplifier Foundation `transcript.jsonl`.
This is the lowest-level building block — isolated, pure I/O, no route dependencies.

**Files:**
- Create: `distro-server/src/amplifier_distro/server/apps/voice/transcript/context.py`
- Create: `distro-server/tests/apps/voice/test_voice_context.py`

---

**Step 1: Write the failing tests**

Create `distro-server/tests/apps/voice/test_voice_context.py`:

```python
"""Unit tests for voice transcript context helpers."""
from __future__ import annotations

import json
from pathlib import Path

import pytest


class TestExtractTextTurns:
    """Tests for _extract_text_turns()."""

    def _write_jsonl(self, path: Path, lines: list[dict]) -> None:
        path.write_text(
            "\n".join(json.dumps(line) for line in lines) + "\n"
        )

    def test_returns_user_and_assistant_turns(self, tmp_path: Path) -> None:
        """Extracts role/text pairs for user and assistant entries."""
        from amplifier_distro.server.apps.voice.transcript.context import (
            _extract_text_turns,
        )

        transcript = tmp_path / "transcript.jsonl"
        self._write_jsonl(transcript, [
            {"role": "user", "content": [{"type": "text", "text": "Hello"}]},
            {"role": "assistant", "content": [{"type": "text", "text": "Hi there"}]},
        ])

        result = _extract_text_turns(transcript)

        assert result == [
            {"role": "user", "text": "Hello"},
            {"role": "assistant", "text": "Hi there"},
        ]

    def test_strips_tool_use_only_entries(self, tmp_path: Path) -> None:
        """Entries whose content contains only tool_use blocks are skipped."""
        from amplifier_distro.server.apps.voice.transcript.context import (
            _extract_text_turns,
        )

        transcript = tmp_path / "transcript.jsonl"
        self._write_jsonl(transcript, [
            {"role": "user", "content": [{"type": "text", "text": "Run it"}]},
            {"role": "assistant", "content": [{"type": "tool_use", "id": "x", "name": "bash", "input": {}}]},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "x", "content": "done"}]},
            {"role": "assistant", "content": [{"type": "text", "text": "Done!"}]},
        ])

        result = _extract_text_turns(transcript)

        # tool_use and tool_result entries are stripped
        assert len(result) == 2
        assert result[0] == {"role": "user", "text": "Run it"}
        assert result[1] == {"role": "assistant", "text": "Done!"}

    def test_missing_file_returns_empty_list(self, tmp_path: Path) -> None:
        """Returns [] when transcript file does not exist."""
        from amplifier_distro.server.apps.voice.transcript.context import (
            _extract_text_turns,
        )

        result = _extract_text_turns(tmp_path / "nonexistent.jsonl")

        assert result == []

    def test_malformed_json_line_skipped(self, tmp_path: Path) -> None:
        """Malformed lines are skipped; valid lines are still returned."""
        from amplifier_distro.server.apps.voice.transcript.context import (
            _extract_text_turns,
        )

        transcript = tmp_path / "transcript.jsonl"
        transcript.write_text(
            '{"role": "user", "content": [{"type": "text", "text": "Hi"}]}\n'
            'this is not json\n'
            '{"role": "assistant", "content": [{"type": "text", "text": "Hello"}]}\n'
        )

        result = _extract_text_turns(transcript)

        assert len(result) == 2

    def test_legacy_string_content_is_extracted(self, tmp_path: Path) -> None:
        """Legacy plain-string content field is handled."""
        from amplifier_distro.server.apps.voice.transcript.context import (
            _extract_text_turns,
        )

        transcript = tmp_path / "transcript.jsonl"
        self._write_jsonl(transcript, [
            {"role": "user", "content": "Old-style plain string"},
        ])

        result = _extract_text_turns(transcript)

        assert result == [{"role": "user", "text": "Old-style plain string"}]

    def test_empty_file_returns_empty_list(self, tmp_path: Path) -> None:
        """Empty file returns []."""
        from amplifier_distro.server.apps.voice.transcript.context import (
            _extract_text_turns,
        )

        transcript = tmp_path / "transcript.jsonl"
        transcript.write_text("")

        result = _extract_text_turns(transcript)

        assert result == []
```

**Step 2: Run tests to confirm they fail**

```bash
cd distro-server
pytest tests/apps/voice/test_voice_context.py::TestExtractTextTurns -v
```

Expected: `ModuleNotFoundError` or `ImportError` — `context.py` does not exist yet.

**Step 3: Create the implementation**

Create `distro-server/src/amplifier_distro/server/apps/voice/transcript/context.py`:

```python
"""Context helpers for chat-to-voice session handoff.

Converts Amplifier Foundation session files (transcript.jsonl, handoff.md,
metadata.json) into OpenAI Realtime API conversation items for injection at
voice session resumption.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from amplifier_distro.conventions import (
    AMPLIFIER_HOME,
    METADATA_FILENAME,
    PROJECTS_DIR,
    TRANSCRIPT_FILENAME,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Transcript normalizer
# ---------------------------------------------------------------------------


def _extract_text_turns(transcript_path: Path) -> list[dict[str, str]]:
    """Read an Amplifier transcript.jsonl and return plain {role, text} pairs.

    - Skips entries whose content contains only tool_use or tool_result blocks.
    - Handles both list-format content and legacy plain-string content.
    - Returns [] on missing file or parse errors; never raises.
    """
    if not transcript_path.exists():
        return []

    turns: list[dict[str, str]] = []
    for line in transcript_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry: dict[str, Any] = json.loads(line)
        except json.JSONDecodeError:
            logger.debug("Skipping malformed transcript line")
            continue

        role = entry.get("role", "")
        if role not in ("user", "assistant"):
            continue

        content = entry.get("content", "")

        # Legacy plain-string format
        if isinstance(content, str):
            if content:
                turns.append({"role": role, "text": content})
            continue

        # List-format: extract text blocks, skip tool_use/tool_result-only entries
        if isinstance(content, list):
            text_parts = [
                block.get("text", "")
                for block in content
                if block.get("type") == "text" and block.get("text")
            ]
            if text_parts:
                turns.append({"role": role, "text": " ".join(text_parts)})
            # If no text blocks (e.g. only tool_use), skip the entry

    return turns
```

**Step 4: Run tests to confirm they pass**

```bash
cd distro-server
pytest tests/apps/voice/test_voice_context.py::TestExtractTextTurns -v
```

Expected: All 6 tests PASS.

**Step 5: Commit**

```bash
cd distro-server
git add src/amplifier_distro/server/apps/voice/transcript/context.py \
        tests/apps/voice/test_voice_context.py
git commit -m "feat: add _extract_text_turns() for chat transcript normalization"
```

---

### Task 2: Add `_read_session_created_by_app()` + unit tests

Read the `created_by_app` discriminator from a Foundation `metadata.json`.
Returns `""` for missing files or missing field — the non-voice fallback.

**Files:**
- Modify: `distro-server/src/amplifier_distro/server/apps/voice/transcript/context.py`
- Modify: `distro-server/tests/apps/voice/test_voice_context.py`

---

**Step 1: Write the failing tests**

Append to `distro-server/tests/apps/voice/test_voice_context.py`:

```python
class TestReadSessionCreatedByApp:
    """Tests for _read_session_created_by_app()."""

    def test_returns_field_when_present(self, tmp_path: Path) -> None:
        """Returns the created_by_app value from metadata.json."""
        from amplifier_distro.server.apps.voice.transcript.context import (
            _read_session_created_by_app,
        )

        session_dir = tmp_path / "projects" / "proj1" / "sessions" / "sess1"
        session_dir.mkdir(parents=True)
        (session_dir / "metadata.json").write_text(
            '{"created_by_app": "chat", "session_id": "sess1"}'
        )

        result = _read_session_created_by_app(
            "sess1", "proj1", amplifier_home=tmp_path
        )

        assert result == "chat"

    def test_returns_empty_string_when_field_missing(self, tmp_path: Path) -> None:
        """Returns '' when metadata.json exists but lacks created_by_app."""
        from amplifier_distro.server.apps.voice.transcript.context import (
            _read_session_created_by_app,
        )

        session_dir = tmp_path / "projects" / "proj1" / "sessions" / "sess1"
        session_dir.mkdir(parents=True)
        (session_dir / "metadata.json").write_text('{"session_id": "sess1"}')

        result = _read_session_created_by_app(
            "sess1", "proj1", amplifier_home=tmp_path
        )

        assert result == ""

    def test_returns_empty_string_when_file_missing(self, tmp_path: Path) -> None:
        """Returns '' when metadata.json does not exist."""
        from amplifier_distro.server.apps.voice.transcript.context import (
            _read_session_created_by_app,
        )

        result = _read_session_created_by_app(
            "sess1", "proj1", amplifier_home=tmp_path
        )

        assert result == ""

    def test_returns_empty_string_on_malformed_json(self, tmp_path: Path) -> None:
        """Returns '' when metadata.json is not valid JSON."""
        from amplifier_distro.server.apps.voice.transcript.context import (
            _read_session_created_by_app,
        )

        session_dir = tmp_path / "projects" / "proj1" / "sessions" / "sess1"
        session_dir.mkdir(parents=True)
        (session_dir / "metadata.json").write_text("this is not json")

        result = _read_session_created_by_app(
            "sess1", "proj1", amplifier_home=tmp_path
        )

        assert result == ""

    def test_reads_voice_correctly(self, tmp_path: Path) -> None:
        """Returns 'voice' when that is the stored value."""
        from amplifier_distro.server.apps.voice.transcript.context import (
            _read_session_created_by_app,
        )

        session_dir = tmp_path / "projects" / "proj1" / "sessions" / "sess1"
        session_dir.mkdir(parents=True)
        (session_dir / "metadata.json").write_text('{"created_by_app": "voice"}')

        result = _read_session_created_by_app(
            "sess1", "proj1", amplifier_home=tmp_path
        )

        assert result == "voice"
```

**Step 2: Run tests to confirm they fail**

```bash
cd distro-server
pytest tests/apps/voice/test_voice_context.py::TestReadSessionCreatedByApp -v
```

Expected: `ImportError` — function not yet defined.

**Step 3: Add implementation to `context.py`**

Append to `distro-server/src/amplifier_distro/server/apps/voice/transcript/context.py`:

```python
# ---------------------------------------------------------------------------
# Session metadata reader
# ---------------------------------------------------------------------------


def _read_session_created_by_app(
    session_id: str,
    project_id: str,
    *,
    amplifier_home: Path | None = None,
) -> str:
    """Read created_by_app from metadata.json for a Foundation session.

    Returns "" when:
    - metadata.json does not exist
    - the field is absent
    - the file is malformed JSON
    Never raises.
    """
    home = amplifier_home or Path(AMPLIFIER_HOME).expanduser()
    metadata_path = (
        home / PROJECTS_DIR / project_id / "sessions" / session_id / METADATA_FILENAME
    )
    if not metadata_path.exists():
        return ""
    try:
        data: dict[str, Any] = json.loads(metadata_path.read_text(encoding="utf-8"))
        return str(data.get("created_by_app", ""))
    except Exception:  # noqa: BLE001
        logger.debug("Failed to read created_by_app from %s", metadata_path)
        return ""
```

**Step 4: Run tests to confirm they pass**

```bash
cd distro-server
pytest tests/apps/voice/test_voice_context.py::TestReadSessionCreatedByApp -v
```

Expected: All 5 tests PASS.

**Step 5: Commit**

```bash
cd distro-server
git add src/amplifier_distro/server/apps/voice/transcript/context.py \
        tests/apps/voice/test_voice_context.py
git commit -m "feat: add _read_session_created_by_app() metadata reader"
```

---

### Task 3: Add `_build_chat_context()` + unit tests

Build the list of Realtime API items for a non-voice session: a preamble item
(from `handoff.md` or a synthetic fallback) plus the last N text turns.

**Files:**
- Modify: `distro-server/src/amplifier_distro/server/apps/voice/transcript/context.py`
- Modify: `distro-server/tests/apps/voice/test_voice_context.py`

---

**Step 1: Write the failing tests**

Append to `distro-server/tests/apps/voice/test_voice_context.py`:

```python
class TestBuildChatContext:
    """Tests for _build_chat_context()."""

    def _make_session_dir(
        self,
        tmp_path: Path,
        project_id: str = "proj1",
        session_id: str = "sess1",
    ) -> Path:
        session_dir = (
            tmp_path / "projects" / project_id / "sessions" / session_id
        )
        session_dir.mkdir(parents=True)
        return session_dir

    def test_handoff_present_first_item_contains_it(self, tmp_path: Path) -> None:
        """When handoff.md exists, item 0 text contains its content."""
        from amplifier_distro.server.apps.voice.transcript.context import (
            _build_chat_context,
        )

        session_dir = self._make_session_dir(tmp_path)
        (session_dir / "handoff.md").write_text("## Summary\nWe discussed caching.")
        (session_dir / "transcript.jsonl").write_text("")

        items = _build_chat_context(
            "sess1", "proj1", amplifier_home=tmp_path
        )

        assert len(items) >= 1
        preamble_text = items[0]["content"][0]["text"]
        assert "We discussed caching." in preamble_text

    def test_absent_handoff_produces_synthetic_preamble(self, tmp_path: Path) -> None:
        """When handoff.md is missing, a synthetic preamble is emitted."""
        from amplifier_distro.server.apps.voice.transcript.context import (
            _build_chat_context,
        )

        session_dir = self._make_session_dir(tmp_path)
        (session_dir / "transcript.jsonl").write_text("")

        items = _build_chat_context(
            "sess1", "proj1", amplifier_home=tmp_path
        )

        assert len(items) >= 1
        preamble_text = items[0]["content"][0]["text"]
        assert "Resuming" in preamble_text or "text chat" in preamble_text.lower()

    def test_recent_turns_truncation(self, tmp_path: Path) -> None:
        """Only the last recent_turns text turns are included."""
        from amplifier_distro.server.apps.voice.transcript.context import (
            _build_chat_context,
        )

        import json as _json

        session_dir = self._make_session_dir(tmp_path)
        # Write 12 turns, but recent_turns=3 means only last 3 are returned
        lines = []
        for i in range(12):
            role = "user" if i % 2 == 0 else "assistant"
            lines.append(
                _json.dumps({"role": role, "content": [{"type": "text", "text": f"msg{i}"}]})
            )
        (session_dir / "transcript.jsonl").write_text("\n".join(lines) + "\n")

        items = _build_chat_context(
            "sess1", "proj1", amplifier_home=tmp_path, recent_turns=3
        )

        # 1 preamble + 3 turns
        assert len(items) == 4
        texts = [item["content"][0]["text"] for item in items[1:]]
        assert "msg9" in texts
        assert "msg10" in texts
        assert "msg11" in texts

    def test_default_recent_turns_is_8(self, tmp_path: Path) -> None:
        """Default recent_turns is 8."""
        from amplifier_distro.server.apps.voice.transcript.context import (
            _build_chat_context,
        )

        import json as _json

        session_dir = self._make_session_dir(tmp_path)
        lines = [
            _json.dumps({"role": "user", "content": [{"type": "text", "text": f"msg{i}"}]})
            for i in range(20)
        ]
        (session_dir / "transcript.jsonl").write_text("\n".join(lines) + "\n")

        items = _build_chat_context("sess1", "proj1", amplifier_home=tmp_path)

        # 1 preamble + 8 turns
        assert len(items) == 9

    def test_no_raises_with_empty_transcript(self, tmp_path: Path) -> None:
        """Does not raise when transcript.jsonl is empty."""
        from amplifier_distro.server.apps.voice.transcript.context import (
            _build_chat_context,
        )

        session_dir = self._make_session_dir(tmp_path)
        (session_dir / "transcript.jsonl").write_text("")

        items = _build_chat_context("sess1", "proj1", amplifier_home=tmp_path)

        # At minimum returns the preamble item
        assert len(items) >= 1

    def test_no_raises_with_missing_transcript(self, tmp_path: Path) -> None:
        """Does not raise when transcript.jsonl is absent."""
        from amplifier_distro.server.apps.voice.transcript.context import (
            _build_chat_context,
        )

        self._make_session_dir(tmp_path)

        items = _build_chat_context("sess1", "proj1", amplifier_home=tmp_path)

        assert len(items) >= 1

    def test_tool_calls_stripped_from_turns(self, tmp_path: Path) -> None:
        """Tool call entries do not appear in the returned items."""
        from amplifier_distro.server.apps.voice.transcript.context import (
            _build_chat_context,
        )

        import json as _json

        session_dir = self._make_session_dir(tmp_path)
        lines = [
            _json.dumps({"role": "user", "content": [{"type": "text", "text": "Run it"}]}),
            _json.dumps({"role": "assistant", "content": [{"type": "tool_use", "id": "x", "name": "bash", "input": {}}]}),
            _json.dumps({"role": "assistant", "content": [{"type": "text", "text": "Done"}]}),
        ]
        (session_dir / "transcript.jsonl").write_text("\n".join(lines) + "\n")

        items = _build_chat_context("sess1", "proj1", amplifier_home=tmp_path)

        # Only the text turns appear (tool_use entry is stripped)
        turn_roles = [item["role"] for item in items[1:]]
        turn_texts = [item["content"][0]["text"] for item in items[1:]]
        assert "Run it" in turn_texts
        assert "Done" in turn_texts
        # No item with tool_use content
        for item in items:
            assert item["content"][0]["type"] in ("text", "input_text")

    def test_user_turns_use_input_text_type(self, tmp_path: Path) -> None:
        """User turn items use content type 'input_text'."""
        from amplifier_distro.server.apps.voice.transcript.context import (
            _build_chat_context,
        )

        import json as _json

        session_dir = self._make_session_dir(tmp_path)
        lines = [
            _json.dumps({"role": "user", "content": [{"type": "text", "text": "Hi"}]}),
        ]
        (session_dir / "transcript.jsonl").write_text("\n".join(lines) + "\n")

        items = _build_chat_context("sess1", "proj1", amplifier_home=tmp_path)

        user_items = [i for i in items if i.get("role") == "user"]
        assert len(user_items) == 1
        assert user_items[0]["content"][0]["type"] == "input_text"

    def test_assistant_turns_use_text_type(self, tmp_path: Path) -> None:
        """Assistant turn items use content type 'text'."""
        from amplifier_distro.server.apps.voice.transcript.context import (
            _build_chat_context,
        )

        import json as _json

        session_dir = self._make_session_dir(tmp_path)
        lines = [
            _json.dumps({"role": "assistant", "content": [{"type": "text", "text": "Hello"}]}),
        ]
        (session_dir / "transcript.jsonl").write_text("\n".join(lines) + "\n")

        items = _build_chat_context("sess1", "proj1", amplifier_home=tmp_path)

        asst_items = [i for i in items if i.get("role") == "assistant"]
        assert len(asst_items) == 1
        assert asst_items[0]["content"][0]["type"] == "text"
```

**Step 2: Run tests to confirm they fail**

```bash
cd distro-server
pytest tests/apps/voice/test_voice_context.py::TestBuildChatContext -v
```

Expected: `ImportError` — function not yet defined.

**Step 3: Add implementation to `context.py`**

Append to `distro-server/src/amplifier_distro/server/apps/voice/transcript/context.py`:

```python
# ---------------------------------------------------------------------------
# Chat context builder
# ---------------------------------------------------------------------------

_SYNTHETIC_PREAMBLE = (
    "Resuming a text chat session. Here are the most recent exchanges:"
)
_HANDOFF_PREFIX = "Context from prior text session:\n\n"


def _build_chat_context(
    session_id: str,
    project_id: str,
    *,
    amplifier_home: Path | None = None,
    recent_turns: int = 8,
) -> list[dict[str, Any]]:
    """Build Realtime API context items for a non-voice (chat/CLI/Slack) session.

    Item 0 is a preamble from handoff.md, or a synthetic fallback.
    Items 1..N are the last `recent_turns` text turns from transcript.jsonl.
    Never raises — errors are logged and a minimal list (preamble only) is returned.
    """
    home = amplifier_home or Path(AMPLIFIER_HOME).expanduser()
    session_dir = home / PROJECTS_DIR / project_id / "sessions" / session_id

    # --- Item 0: preamble ---
    handoff_path = session_dir / "handoff.md"
    try:
        if handoff_path.exists():
            preamble_text = _HANDOFF_PREFIX + handoff_path.read_text(encoding="utf-8")
        else:
            preamble_text = _SYNTHETIC_PREAMBLE
    except Exception:  # noqa: BLE001
        logger.warning("Failed to read handoff.md for session %s", session_id)
        preamble_text = _SYNTHETIC_PREAMBLE

    items: list[dict[str, Any]] = [
        {
            "type": "message",
            "role": "assistant",
            "content": [{"type": "text", "text": preamble_text}],
        }
    ]

    # --- Items 1..N: recent text turns ---
    try:
        transcript_path = session_dir / TRANSCRIPT_FILENAME
        turns = _extract_text_turns(transcript_path)
        for turn in turns[-recent_turns:]:
            content_type = "input_text" if turn["role"] == "user" else "text"
            items.append(
                {
                    "type": "message",
                    "role": turn["role"],
                    "content": [{"type": content_type, "text": turn["text"]}],
                }
            )
    except Exception:  # noqa: BLE001
        logger.warning("Failed to build turn context for session %s", session_id)

    return items
```

**Step 4: Run tests to confirm they pass**

```bash
cd distro-server
pytest tests/apps/voice/test_voice_context.py::TestBuildChatContext -v
```

Expected: All 9 tests PASS.

**Step 5: Commit**

```bash
cd distro-server
git add src/amplifier_distro/server/apps/voice/transcript/context.py \
        tests/apps/voice/test_voice_context.py
git commit -m "feat: add _build_chat_context() for chat→voice context injection"
```

---

### Task 4: Add `_build_context_for_resume()` dispatcher + unit tests

Branch on `created_by_app`: voice → return existing voice context unchanged;
anything else (including `""`) → call `_build_chat_context()`.

**Files:**
- Modify: `distro-server/src/amplifier_distro/server/apps/voice/transcript/context.py`
- Modify: `distro-server/tests/apps/voice/test_voice_context.py`

---

**Step 1: Write the failing tests**

Append to `distro-server/tests/apps/voice/test_voice_context.py`:

```python
class TestBuildContextForResume:
    """Tests for _build_context_for_resume()."""

    def test_voice_returns_voice_context_unchanged(self, tmp_path: Path) -> None:
        """When created_by_app is 'voice', voice_context is returned as-is."""
        from amplifier_distro.server.apps.voice.transcript.context import (
            _build_context_for_resume,
        )

        voice_ctx = [{"type": "message", "role": "user", "content": [{"type": "input_text", "text": "hi"}]}]

        result = _build_context_for_resume(
            "sess1", "proj1", "voice", voice_ctx, amplifier_home=tmp_path
        )

        assert result is voice_ctx

    def test_chat_dispatches_to_build_chat_context(self, tmp_path: Path) -> None:
        """When created_by_app is 'chat', returns chat context (not voice_ctx)."""
        from amplifier_distro.server.apps.voice.transcript.context import (
            _build_context_for_resume,
        )

        session_dir = tmp_path / "projects" / "proj1" / "sessions" / "sess1"
        session_dir.mkdir(parents=True)
        (session_dir / "transcript.jsonl").write_text("")

        voice_ctx = [{"type": "THIS_SHOULD_NOT_APPEAR"}]

        result = _build_context_for_resume(
            "sess1", "proj1", "chat", voice_ctx, amplifier_home=tmp_path
        )

        # Must not return the voice_ctx sentinel
        assert result is not voice_ctx
        # Must return at least a preamble item
        assert len(result) >= 1

    def test_empty_created_by_app_takes_chat_path(self, tmp_path: Path) -> None:
        """Empty created_by_app (legacy session) routes to chat path."""
        from amplifier_distro.server.apps.voice.transcript.context import (
            _build_context_for_resume,
        )

        session_dir = tmp_path / "projects" / "proj1" / "sessions" / "sess1"
        session_dir.mkdir(parents=True)
        (session_dir / "transcript.jsonl").write_text("")

        voice_ctx = [{"type": "THIS_SHOULD_NOT_APPEAR"}]

        result = _build_context_for_resume(
            "sess1", "proj1", "", voice_ctx, amplifier_home=tmp_path
        )

        assert result is not voice_ctx
        assert len(result) >= 1

    def test_slack_takes_chat_path(self, tmp_path: Path) -> None:
        """'slack' created_by_app routes to the chat path."""
        from amplifier_distro.server.apps.voice.transcript.context import (
            _build_context_for_resume,
        )

        session_dir = tmp_path / "projects" / "proj1" / "sessions" / "sess1"
        session_dir.mkdir(parents=True)
        (session_dir / "transcript.jsonl").write_text("")

        voice_ctx: list[dict[str, Any]] = []

        result = _build_context_for_resume(
            "sess1", "proj1", "slack", voice_ctx, amplifier_home=tmp_path
        )

        assert result is not voice_ctx
        assert len(result) >= 1
```

**Step 2: Run tests to confirm they fail**

```bash
cd distro-server
pytest tests/apps/voice/test_voice_context.py::TestBuildContextForResume -v
```

Expected: `ImportError` — function not yet defined.

**Step 3: Add implementation to `context.py`**

Add the `Any` import (already present) and append this function:

```python
# ---------------------------------------------------------------------------
# Context dispatcher
# ---------------------------------------------------------------------------


def _build_context_for_resume(
    session_id: str,
    project_id: str,
    created_by_app: str,
    voice_context: list[dict[str, Any]],
    *,
    amplifier_home: Path | None = None,
    recent_turns: int = 8,
) -> list[dict[str, Any]]:
    """Dispatch to the right context builder based on created_by_app.

    - "voice"      → return voice_context unchanged (existing path)
    - anything else → call _build_chat_context() (new path)
    """
    if created_by_app == "voice":
        return voice_context
    return _build_chat_context(
        session_id,
        project_id,
        amplifier_home=amplifier_home,
        recent_turns=recent_turns,
    )
```

**Step 4: Run all context tests**

```bash
cd distro-server
pytest tests/apps/voice/test_voice_context.py -v
```

Expected: All tests in all 4 classes PASS (20+ tests total).

**Step 5: Commit**

```bash
cd distro-server
git add src/amplifier_distro/server/apps/voice/transcript/context.py \
        tests/apps/voice/test_voice_context.py
git commit -m "feat: add _build_context_for_resume() dispatcher (Phase 2 complete)"
```

---

### Task 5: Stamp `created_by_app: "voice"` on session creation

New voice sessions must carry `"created_by_app": "voice"` in their `metadata.json`
so that future resumes can identify them as voice sessions.

**Files:**
- Modify: `distro-server/src/amplifier_distro/server/apps/voice/transcript/repository.py`
- Modify: `distro-server/tests/apps/voice/test_voice_routes.py`

---

**Step 1: Write the failing test**

Find the `TestSessionLifecycle` class (or nearest appropriate class) in
`distro-server/tests/apps/voice/test_voice_routes.py` and add:

```python
# Inside an appropriate test class in test_voice_routes.py
# (add alongside existing create_session tests or in TestSessionLifecycle)

class TestCreateSessionStampsApp:
    """POST /sessions writes created_by_app: 'voice' to metadata.json."""

    def setup_method(self) -> None:
        import amplifier_distro.server.apps.voice as voice_module
        from amplifier_distro.server.apps.voice.transcript.repository import (
            VoiceConversationRepository,
        )

        self._tmp_dir = None  # set in test via tmp_path

    def test_create_session_writes_created_by_app_voice(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """POST /sessions stamps created_by_app='voice' in Amplifier metadata.json."""
        import json
        import amplifier_distro.server.apps.voice as voice_module
        from amplifier_distro.server.apps.voice.transcript.repository import (
            VoiceConversationRepository,
        )
        from unittest.mock import MagicMock, AsyncMock

        # Arrange: override repo to use tmp_path and fake backend
        repo = VoiceConversationRepository(base_dir=tmp_path / "voice-sessions")
        old_repo = voice_module._repo_override
        old_backend = voice_module._backend_override

        fake_backend = MagicMock()
        fake_session = MagicMock()
        fake_session.session_id = "test-session-99"
        fake_session.coordinator = MagicMock()
        fake_session.coordinator.register_capability = MagicMock()
        fake_backend.create_session = AsyncMock(return_value=fake_session)
        fake_backend.register_hooks = MagicMock(return_value=lambda: None)

        voice_module._repo_override = repo
        voice_module._backend_override = fake_backend

        try:
            app = _make_app()
            from starlette.testclient import TestClient
            client = TestClient(app, raise_server_exceptions=False)

            resp = client.post(
                "/apps/voice/sessions",
                json={"workspace_root": str(tmp_path)},
            )
            assert resp.status_code == 200
            session_id = resp.json()["session_id"]

            # The Amplifier metadata.json for this session must have created_by_app
            from amplifier_distro.conventions import AMPLIFIER_HOME, PROJECTS_DIR
            project_id = str(tmp_path).replace("/", "-")
            metadata_path = (
                tmp_path
                / PROJECTS_DIR
                / project_id
                / "sessions"
                / session_id
                / "metadata.json"
            )
            # NOTE: metadata.json is written to AMPLIFIER_HOME, not tmp_path.
            # For this test, patch amplifier_home or check via the repo's
            # write_amplifier_metadata path. The assertion is that the field exists.
            #
            # Since write_amplifier_metadata uses AMPLIFIER_HOME (not overridable),
            # this test verifies the field is in the written dict by mocking
            # write_amplifier_metadata and inspecting what was passed.
            # See implementation notes.

        finally:
            voice_module._repo_override = old_repo
            voice_module._backend_override = old_backend
            voice_module._active_connection = None
```

> **Implementation note:** `write_amplifier_metadata` currently uses the global
> `AMPLIFIER_HOME`. The cleanest way to add `created_by_app` is to extend
> `write_amplifier_metadata` to accept the field and pass it in during `create_session`.
> The test can be simplified to assert the metadata dict passed to the write method
> contains `created_by_app: "voice"` using a mock spy, or the method can be made
> testable via `amplifier_home` override (matching the pattern in `_read_session_created_by_app`).

**Step 2: Run test to confirm it fails**

```bash
cd distro-server
pytest tests/apps/voice/test_voice_routes.py::TestCreateSessionStampsApp -v
```

**Step 3: Implement the change in `repository.py`**

In `VoiceConversationRepository.write_amplifier_metadata`, add `created_by_app` to
the written dict:

Find this section in `repository.py`:
```python
        metadata: dict[str, str | int] = {
            "session_id": session_id,
            "bundle": "voice",
            "name": conv.title,
            "created": conv.created_at.isoformat(),
            "model": "voice",
            "turn_count": 0,
        }
```

Replace with:
```python
        metadata: dict[str, str | int] = {
            "session_id": session_id,
            "bundle": "voice",
            "name": conv.title,
            "created": conv.created_at.isoformat(),
            "model": "voice",
            "turn_count": 0,
            "created_by_app": "voice",
        }
```

**Step 4: Run test to confirm it passes**

```bash
cd distro-server
pytest tests/apps/voice/test_voice_routes.py::TestCreateSessionStampsApp -v
```

**Step 5: Confirm existing transcript tests still pass**

```bash
cd distro-server
pytest tests/apps/voice/test_voice_transcript.py -v
```

Expected: All existing tests PASS.

**Step 6: Commit**

```bash
cd distro-server
git add src/amplifier_distro/server/apps/voice/transcript/repository.py \
        tests/apps/voice/test_voice_routes.py
git commit -m "feat: stamp created_by_app='voice' in Amplifier metadata.json on create"
```

---

### Task 6: Wire `resume_session()` with the context dispatcher

Replace the direct `repo.get_resumption_context()` call with the dispatcher.
Adds cross-process session fallback (OQ-4 resolution).

**Files:**
- Modify: `distro-server/src/amplifier_distro/server/apps/voice/__init__.py`
- Modify: `distro-server/tests/apps/voice/test_voice_routes.py`

---

**Step 1: Write the failing integration test**

Add to `test_voice_routes.py` (inside a `TestResumeSession` class or standalone):

```python
class TestResumeChatSession:
    """Resuming a chat session injects context_to_inject from handoff.md."""

    def test_resume_chat_session_injects_context(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """POST /sessions/{id}/resume for a chat session returns handoff context."""
        import json
        import amplifier_distro.server.apps.voice as voice_module
        from unittest.mock import MagicMock, AsyncMock
        from amplifier_distro.server.session_backend import SessionInfo
        from amplifier_distro.server.apps.voice.transcript.repository import (
            VoiceConversationRepository,
        )
        from amplifier_distro import stub as stub_module

        # Set up a fake Amplifier session directory with handoff.md
        # project_id is derived from workspace_root (path separators → "-")
        project_id = str(tmp_path).replace("/", "-")
        session_id = "chat-sess-001"
        session_dir = (
            tmp_path / "projects" / project_id / "sessions" / session_id
        )
        session_dir.mkdir(parents=True)
        (session_dir / "metadata.json").write_text(
            json.dumps({"created_by_app": "chat", "session_id": session_id})
        )
        (session_dir / "handoff.md").write_text("We discussed the cache design.")
        (session_dir / "transcript.jsonl").write_text("")

        # Patch _read_session_created_by_app to point at our tmp_path
        from amplifier_distro.server.apps.voice.transcript import context as ctx_mod
        original_build = ctx_mod._build_context_for_resume

        def patched_build(sid, pid, app, vctx, **kwargs):
            return original_build(sid, pid, app, vctx, amplifier_home=tmp_path, **kwargs)

        monkeypatch.setattr(ctx_mod, "_build_context_for_resume", patched_build)

        original_read = ctx_mod._read_session_created_by_app

        def patched_read(sid, pid, **kwargs):
            return original_read(sid, pid, amplifier_home=tmp_path, **kwargs)

        monkeypatch.setattr(ctx_mod, "_read_session_created_by_app", patched_read)

        # Fake backend that knows about the session
        fake_backend = MagicMock()
        fake_backend.get_session_info = AsyncMock(
            return_value=SessionInfo(
                session_id=session_id,
                project_id=project_id,
                working_dir=str(tmp_path),
            )
        )
        fake_backend.resume_session = AsyncMock()

        repo = VoiceConversationRepository(base_dir=tmp_path / "voice-sessions")

        old_repo = voice_module._repo_override
        old_backend = voice_module._backend_override
        voice_module._repo_override = repo
        voice_module._backend_override = fake_backend

        try:
            stub_module._stub_mode = True
            app = _make_app()
            from starlette.testclient import TestClient
            client = TestClient(app, raise_server_exceptions=False)

            resp = client.post(f"/apps/voice/sessions/{session_id}/resume")
            assert resp.status_code == 200

            data = resp.json()
            assert "context_to_inject" in data
            injected = data["context_to_inject"]
            # At minimum: preamble item referencing handoff
            assert len(injected) >= 1
            preamble = injected[0]
            assert "cache design" in preamble["content"][0]["text"]

        finally:
            stub_module._stub_mode = False
            voice_module._repo_override = old_repo
            voice_module._backend_override = old_backend
            voice_module._active_connection = None
```

**Step 2: Run test to confirm it fails**

```bash
cd distro-server
pytest tests/apps/voice/test_voice_routes.py::TestResumeChatSession -v
```

Expected: FAIL — `resume_session` route doesn't use the dispatcher yet.

**Step 3: Modify `resume_session()` in `__init__.py`**

Add the import at the top of the file (after other imports):

```python
from amplifier_distro.server.apps.voice.transcript.context import (
    _build_context_for_resume,
    _read_session_created_by_app,
)
```

Find the `resume_session` route body and replace the single `context` line:

**Old code** (around line 428):
```python
    # Pull transcript context for the Realtime API before resuming
    context = repo.get_resumption_context(session_id)
```

**New code:**
```python
    # Determine session type and build appropriate context for the Realtime API
    project_id: str = session_info.project_id or ""
    created_by_app = _read_session_created_by_app(session_id, project_id)
    voice_context = repo.get_resumption_context(session_id)
    context = _build_context_for_resume(
        session_id, project_id, created_by_app, voice_context
    )
```

> **OQ-4 Note:** For non-voice sessions, `backend.get_session_info()` may return `None`
> because the session was created by a different process. The current route returns 404
> in that case. If cross-process resume is needed (Phase 3 investigation), add a
> filesystem fallback before the 404 return:
> ```python
> if session_info is None:
>     # Fallback: try to read session metadata from disk
>     session_info = _find_session_info_from_disk(session_id)
> if session_info is None:
>     return JSONResponse(status_code=404, ...)
> ```
> For now, the existing 404 path remains; this is addressed in a follow-up once
> the backend's cross-process discovery capability is confirmed.

**Step 4: Run the new test and regression tests**

```bash
cd distro-server
pytest tests/apps/voice/test_voice_routes.py::TestResumeChatSession -v
pytest tests/apps/voice/test_voice_routes.py -v
```

Expected: New test PASS. All existing route tests PASS (voice-to-voice regression clean).

**Step 5: Commit**

```bash
cd distro-server
git add src/amplifier_distro/server/apps/voice/__init__.py \
        tests/apps/voice/test_voice_routes.py
git commit -m "feat: wire _build_context_for_resume() into resume_session route (Phase 3)"
```

---

### Task 7: Widen `list_sessions()` with `_list_amplifier_sessions()`

Add a filesystem scan that reads non-voice sessions from
`~/.amplifier/projects/*/sessions/*/metadata.json` and merges them
with the voice index. Voice sessions get a `created_by_app: "voice"` tag
via `setdefault`.

**Files:**
- Modify: `distro-server/src/amplifier_distro/server/apps/voice/__init__.py`
- Modify: `distro-server/tests/apps/voice/test_voice_routes.py`

---

**Step 1: Write the failing tests**

Add to `test_voice_routes.py`:

```python
class TestListSessionsWidened:
    """GET /sessions returns voice + non-voice sessions merged."""

    def test_sessions_list_includes_non_voice_sessions(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """GET /sessions returns sessions from Amplifier projects dir."""
        import json
        import amplifier_distro.server.apps.voice as voice_module
        from amplifier_distro.server.apps.voice.transcript.repository import (
            VoiceConversationRepository,
        )

        # Set up a fake Amplifier session directory for a chat session
        project_id = "proj-abc"
        session_id = "chat-sess-listed-001"
        session_dir = (
            tmp_path / "projects" / project_id / "sessions" / session_id
        )
        session_dir.mkdir(parents=True)
        (session_dir / "metadata.json").write_text(
            json.dumps({
                "session_id": session_id,
                "created_by_app": "chat",
                "name": "Chat about caching",
                "created": "2026-03-01T10:00:00",
            })
        )

        # Patch _list_amplifier_sessions to use tmp_path
        from amplifier_distro.server.apps import voice as voice_app
        original_list = voice_app._list_amplifier_sessions

        def patched_list(workspace_root):
            return original_list(tmp_path)

        monkeypatch.setattr(voice_app, "_list_amplifier_sessions", patched_list)

        repo = VoiceConversationRepository(base_dir=tmp_path / "voice-sessions")
        old_repo = voice_module._repo_override
        voice_module._repo_override = repo

        try:
            app = _make_app()
            from starlette.testclient import TestClient
            client = TestClient(app, raise_server_exceptions=False)

            resp = client.get("/apps/voice/sessions")
            assert resp.status_code == 200

            sessions = resp.json()
            ids = [s.get("session_id") or s.get("id") for s in sessions]
            assert session_id in ids

        finally:
            voice_module._repo_override = old_repo

    def test_non_voice_sessions_include_created_by_app_label(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """Non-voice sessions have created_by_app set in the list response."""
        import json
        import amplifier_distro.server.apps.voice as voice_module
        from amplifier_distro.server.apps.voice.transcript.repository import (
            VoiceConversationRepository,
        )

        project_id = "proj-abc"
        session_id = "slack-sess-001"
        session_dir = (
            tmp_path / "projects" / project_id / "sessions" / session_id
        )
        session_dir.mkdir(parents=True)
        (session_dir / "metadata.json").write_text(
            json.dumps({
                "session_id": session_id,
                "created_by_app": "slack",
                "name": "Slack session",
                "created": "2026-03-01T10:00:00",
            })
        )

        from amplifier_distro.server.apps import voice as voice_app
        original_list = voice_app._list_amplifier_sessions

        def patched_list(workspace_root):
            return original_list(tmp_path)

        monkeypatch.setattr(voice_app, "_list_amplifier_sessions", patched_list)

        repo = VoiceConversationRepository(base_dir=tmp_path / "voice-sessions")
        old_repo = voice_module._repo_override
        voice_module._repo_override = repo

        try:
            app = _make_app()
            from starlette.testclient import TestClient
            client = TestClient(app, raise_server_exceptions=False)

            resp = client.get("/apps/voice/sessions")
            assert resp.status_code == 200

            sessions = resp.json()
            slack_sessions = [
                s for s in sessions
                if (s.get("session_id") or s.get("id")) == session_id
            ]
            assert len(slack_sessions) == 1
            assert slack_sessions[0].get("created_by_app") == "slack"

        finally:
            voice_module._repo_override = old_repo
```

**Step 2: Run tests to confirm they fail**

```bash
cd distro-server
pytest tests/apps/voice/test_voice_routes.py::TestListSessionsWidened -v
```

Expected: FAIL — `_list_amplifier_sessions` not defined yet.

**Step 3: Add `_list_amplifier_sessions()` helper to `__init__.py`**

Add this function to `__init__.py` in the "Internal helpers" section (after the existing helpers, before the routes):

```python
def _list_amplifier_sessions(workspace_root: Path) -> list[dict[str, Any]]:
    """Scan ~/.amplifier/projects/*/sessions/*/metadata.json for non-voice sessions.

    Returns summary dicts with keys: id, session_id, title, status, created_at,
    created_by_app. Skips sessions where created_by_app == "voice" (those are
    already in the voice index). Errors on individual sessions are logged and
    the session is skipped; never raises.
    """
    home = Path(AMPLIFIER_HOME).expanduser()
    projects_dir = home / PROJECTS_DIR
    sessions: list[dict[str, Any]] = []

    if not projects_dir.exists():
        return sessions

    for metadata_path in projects_dir.glob("*/sessions/*/metadata.json"):
        try:
            import json as _json
            data: dict[str, Any] = _json.loads(
                metadata_path.read_text(encoding="utf-8")
            )
            created_by_app: str = str(data.get("created_by_app", ""))
            if created_by_app == "voice":
                continue  # Already in voice index
            session_id: str = str(
                data.get("session_id") or metadata_path.parent.name
            )
            sessions.append(
                {
                    "id": session_id,
                    "session_id": session_id,
                    "title": str(data.get("name", session_id)),
                    "status": str(data.get("status", "ended")),
                    "created_at": str(data.get("created", "")),
                    "created_by_app": created_by_app,
                }
            )
        except Exception:  # noqa: BLE001
            logger.debug(
                "Skipping unreadable session metadata: %s", metadata_path
            )

    return sessions
```

Add the missing import to the top of `__init__.py` (if not already present):

```python
from amplifier_distro.conventions import AMPLIFIER_HOME, PROJECTS_DIR
```

> **Check:** `AMPLIFIER_HOME` and `PROJECTS_DIR` are already imported in
> `transcript/repository.py` — verify that `__init__.py` also imports them.
> If they are already imported via other means, skip this step.

**Step 4: Modify `list_sessions()` route to merge both sources**

Find the `list_sessions` route in `__init__.py`:

```python
@router.get("/sessions")
async def list_sessions(
    x_api_key: str | None = Header(default=None),
) -> JSONResponse:
    """Return the list of VoiceConversations from the repository index."""
    await _require_api_key(x_api_key)
    repo = _get_repo()
    return JSONResponse(content=repo.list_conversations())
```

Replace with:

```python
@router.get("/sessions")
async def list_sessions(
    x_api_key: str | None = Header(default=None),
) -> JSONResponse:
    """Return voice + non-voice Amplifier sessions merged into one list.

    Voice sessions from the voice-sessions index are tagged
    created_by_app='voice' via setdefault so existing entries are not
    overwritten. Non-voice sessions are scanned from the Amplifier
    projects directory.
    """
    await _require_api_key(x_api_key)
    repo = _get_repo()

    voice_sessions = repo.list_conversations()
    for s in voice_sessions:
        s.setdefault("created_by_app", "voice")

    non_voice_sessions = _list_amplifier_sessions(_get_workspace_root())

    return JSONResponse(content=voice_sessions + non_voice_sessions)
```

**Step 5: Run tests**

```bash
cd distro-server
pytest tests/apps/voice/test_voice_routes.py::TestListSessionsWidened -v
pytest tests/apps/voice/test_voice_routes.py -v
```

Expected: New tests PASS. All existing route tests PASS.

**Step 6: Commit**

```bash
cd distro-server
git add src/amplifier_distro/server/apps/voice/__init__.py \
        tests/apps/voice/test_voice_routes.py
git commit -m "feat: widen list_sessions() to include non-voice Amplifier sessions (Phase 4)"
```

---

### Task 8: Update `SessionPicker` frontend + static tests

Add `APP_LABELS` map and render a muted `created_by_app` label alongside each
session title in the `SessionPicker` component.

**Files:**
- Modify: `distro-server/src/amplifier_distro/server/apps/voice/static/index.html`
- Modify: `distro-server/tests/apps/voice/test_voice_static_index.py`

---

**Step 1: Write the failing static content tests**

Add to `test_voice_static_index.py` (append a new test class):

```python
class TestSessionPickerLabels:
    """SessionPicker renders created_by_app labels from the APP_LABELS map."""

    def test_app_labels_map_present_in_html(self) -> None:
        """APP_LABELS const is defined in index.html."""
        content = INDEX_HTML.read_text()
        assert "APP_LABELS" in content, (
            "APP_LABELS map not found in index.html"
        )

    def test_chat_conversation_label_present(self) -> None:
        """'Chat conversation' label is defined."""
        content = INDEX_HTML.read_text()
        assert "Chat conversation" in content, (
            "'Chat conversation' label missing from APP_LABELS in index.html"
        )

    def test_voice_conversation_label_present(self) -> None:
        """'Voice conversation' label is defined."""
        content = INDEX_HTML.read_text()
        assert "Voice conversation" in content, (
            "'Voice conversation' label missing from APP_LABELS in index.html"
        )

    def test_created_by_app_referenced_in_html(self) -> None:
        """created_by_app field is referenced in the session rendering logic."""
        content = INDEX_HTML.read_text()
        assert "created_by_app" in content, (
            "created_by_app not referenced in index.html session rendering"
        )

    def test_slack_conversation_label_present(self) -> None:
        """'Slack conversation' label is defined."""
        content = INDEX_HTML.read_text()
        assert "Slack conversation" in content

    def test_cli_conversation_label_present(self) -> None:
        """'CLI conversation' label is defined."""
        content = INDEX_HTML.read_text()
        assert "CLI conversation" in content
```

**Step 2: Run tests to confirm they fail**

```bash
cd distro-server
pytest tests/apps/voice/test_voice_static_index.py::TestSessionPickerLabels -v
```

Expected: FAIL — `APP_LABELS` and labels not yet in `index.html`.

**Step 3: Modify `static/index.html`**

Locate the `SessionPicker` component in `index.html`. Add the `APP_LABELS`
constant near the top of the component's script section and update the
session item rendering to display the label.

Find the existing session list rendering (look for `session.title` or the
session list map/render pattern) and add a label display alongside it.

The exact insertion depends on the existing markup. The pattern to add:

```javascript
const APP_LABELS = {
  voice: 'Voice conversation',
  chat:  'Chat conversation',
  slack: 'Slack conversation',
  cli:   'CLI conversation',
};
```

And in the session item render, add a label span:
```javascript
// Alongside the session title rendering:
const appLabel = APP_LABELS[session.created_by_app] || session.created_by_app || '';
// Render: html`<span class="session-app-label">${appLabel}</span>`
```

> The exact location depends on reading the existing Preact component structure in
> `index.html`. Read the file first (`read_file` the component section), then make
> the minimal targeted edit that adds `APP_LABELS` and references `created_by_app`.

**Step 4: Run the tests to confirm they pass**

```bash
cd distro-server
pytest tests/apps/voice/test_voice_static_index.py::TestSessionPickerLabels -v
pytest tests/apps/voice/test_voice_static_index.py -v
```

Expected: New tests PASS. All existing static index tests PASS.

**Step 5: Commit**

```bash
cd distro-server
git add src/amplifier_distro/server/apps/voice/static/index.html \
        tests/apps/voice/test_voice_static_index.py
git commit -m "feat: add APP_LABELS to SessionPicker for chat/voice/slack/cli labels (Phase 4)"
```

---

### Task 9: Type checks, lint, and full regression gate

No new code — this task verifies the complete implementation is clean.

**Files:** None created or modified (fix-up only).

---

**Step 1: Run pyright on all modified files**

```bash
cd distro-server
python -m pyright src/amplifier_distro/server/apps/voice/transcript/context.py \
                  src/amplifier_distro/server/apps/voice/__init__.py \
                  src/amplifier_distro/server/apps/voice/transcript/repository.py
```

Fix any type errors before proceeding. Common issues:
- `Any` import missing from `typing`
- `Path | None` requiring `from __future__ import annotations`
- Return type annotations on new functions

**Step 2: Run ruff on all modified files**

```bash
cd distro-server
python -m ruff check src/amplifier_distro/server/apps/voice/transcript/context.py \
                     src/amplifier_distro/server/apps/voice/__init__.py \
                     src/amplifier_distro/server/apps/voice/transcript/repository.py
python -m ruff format --check src/amplifier_distro/server/apps/voice/transcript/context.py \
                               src/amplifier_distro/server/apps/voice/__init__.py
```

Fix any lint/format issues. Auto-fix is safe: `ruff check --fix` and `ruff format`.

**Step 3: Run the full voice test suite**

```bash
cd distro-server
pytest tests/apps/voice/ -v
```

Expected: All tests pass — no regressions in existing tests.

**Step 4: Run new tests in isolation as a final confirmation**

```bash
cd distro-server
pytest tests/apps/voice/test_voice_context.py -v
pytest tests/apps/voice/test_voice_routes.py::TestCreateSessionStampsApp -v
pytest tests/apps/voice/test_voice_routes.py::TestResumeChatSession -v
pytest tests/apps/voice/test_voice_routes.py::TestListSessionsWidened -v
pytest tests/apps/voice/test_voice_static_index.py::TestSessionPickerLabels -v
```

**Step 5: Commit cleanup fixes (if any)**

```bash
cd distro-server
git add -u
git commit -m "fix: pyright and ruff cleanup for chat-to-voice feature (Phase 5)"
```

---

## Dependencies

Tasks must be executed in order within each phase. Cross-phase ordering:

```
Task 1 → Task 2 → Task 3 → Task 4    (Phase 1–2: pure unit tests, no deps)
                                    ↓
                              Task 5 → Task 6   (Phase 3: route wiring)
                              Task 7             (Phase 4: can parallel with 5–6)
                              Task 8             (Phase 4: can parallel with 5–6)
                                    ↓
                              Task 9             (Phase 5: gate)
```

Tasks 5 and 7 can be started independently once Tasks 1–4 are complete.
Tasks 6 and 8 depend on Tasks 5 and 7 respectively.

---

## Verification

### Automated (regression gate — all must pass)

```bash
cd distro-server
pytest tests/apps/voice/ -v --tb=short
```

### New tests added by this plan

| File | Class | Count |
|------|-------|-------|
| `test_voice_context.py` | `TestExtractTextTurns` | 6 |
| `test_voice_context.py` | `TestReadSessionCreatedByApp` | 5 |
| `test_voice_context.py` | `TestBuildChatContext` | 9 |
| `test_voice_context.py` | `TestBuildContextForResume` | 4 |
| `test_voice_routes.py` | `TestCreateSessionStampsApp` | 1 |
| `test_voice_routes.py` | `TestResumeChatSession` | 1 |
| `test_voice_routes.py` | `TestListSessionsWidened` | 2 |
| `test_voice_static_index.py` | `TestSessionPickerLabels` | 6 |
| **Total new tests** | | **34** |

### Manual checklist (pre-merge)

Run these against a live server after all automated tests pass:

1. **Chat resume:** Open SessionPicker → chat session appears labelled "Chat conversation" → resume → voice model acknowledges prior context
2. **Absent `handoff.md`:** Resume a chat session without `handoff.md` → graceful fallback message "Resuming a text chat session", no crash
3. **Voice regression:** Resume an existing voice session → identical to pre-change behaviour (voice context injected, no preamble)
4. **Legacy session:** Find a session without `created_by_app` in metadata → resume → treated as non-voice, no crash
5. **Empty transcript:** Resume a session with no turns → only preamble injected, voice model proceeds normally

### Open question to resolve before merging (OQ-4)

`FoundationBackend.get_session_info()` only looks up in-memory sessions
(`self._sessions` dict, current process). A non-voice session created by the
chat CLI **will not be found** — the route will currently return 404.

Resolution options (investigate in Phase 3):
- **Option A:** Add a filesystem fallback in `resume_session` that reads the session
  dir directly when `get_session_info` returns `None` and `created_by_app != "voice"`
- **Option B:** Extend `FoundationBackend` to do a filesystem scan for cross-process
  sessions (larger change, impacts shared code)

Option A is preferred (additive, voice-only, no shared code mutation). The fallback
would read `~/.amplifier/projects/*/sessions/{session_id}/metadata.json` to recover
`working_dir`, then proceed to `backend.resume_session`.
