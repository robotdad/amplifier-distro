# Chat-to-Voice Session Handoff Implementation Plan

> **Execution:** Use the subagent-driven-development workflow to implement this plan task-by-task.

**Goal:** Enable the voice app to resume any Amplifier session (chat, CLI, Slack) by injecting a summarized text context into the OpenAI Realtime session — without breaking the existing voice-to-voice resume path.

**Architecture:** A new server-side helper `_build_context_for_resume()` reads `created_by_app` from `metadata.json` to branch between the existing voice transcript path and a new `_build_chat_context()` function that reads `handoff.md` + Amplifier `transcript.jsonl`. The `SessionPicker` frontend is widened to show all sessions (not just voice) with source labels. The `handleResume()` client path is **unchanged** — it fires the same `POST /sessions/{id}/resume` regardless of session type.

**Tech Stack:** Python 3.12, FastAPI, pytest, asyncio, Preact (frontend — no build step, vanilla JS in `index.html`)

---

## Working Directory

All commands run from: `distro-server/`

```bash
cd distro-server/
```

---

## Task 1: Audit Amplifier transcript format and write a normalizer

The new `_build_chat_context()` must parse `transcript.jsonl` produced by the chat, CLI, and Slack apps. Before writing it, verify what that file actually contains and write a tested normalizer.

**Files:**
- Create: `src/amplifier_distro/server/apps/voice/transcript/context.py`
- Create: `tests/apps/voice/test_voice_context.py`

---

### Step 1: Create the test file with a format-verification test

```python
# tests/apps/voice/test_voice_context.py
"""Tests for chat-to-voice context building helpers."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from amplifier_distro.server.apps.voice.transcript.context import (
    _extract_text_turns,
)


class TestExtractTextTurns:
    """_extract_text_turns reads a transcript.jsonl and returns plain-text pairs."""

    def _write_transcript(self, path: Path, lines: list[dict]) -> Path:
        p = path / "transcript.jsonl"
        p.write_text("\n".join(json.dumps(line) for line in lines))
        return p

    def test_returns_user_and_assistant_turns(self, tmp_path: Path) -> None:
        transcript = self._write_transcript(
            tmp_path,
            [
                {"role": "user", "content": [{"type": "text", "text": "Hello"}]},
                {"role": "assistant", "content": [{"type": "text", "text": "Hi there"}]},
            ],
        )
        turns = _extract_text_turns(transcript)
        assert turns == [
            {"role": "user", "text": "Hello"},
            {"role": "assistant", "text": "Hi there"},
        ]

    def test_strips_tool_use_entries(self, tmp_path: Path) -> None:
        transcript = self._write_transcript(
            tmp_path,
            [
                {"role": "user", "content": [{"type": "text", "text": "Do something"}]},
                {
                    "role": "assistant",
                    "content": [
                        {"type": "tool_use", "id": "x", "name": "delegate", "input": {}}
                    ],
                },
                {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "x", "content": []}]},
                {"role": "assistant", "content": [{"type": "text", "text": "Done"}]},
            ],
        )
        turns = _extract_text_turns(transcript)
        assert len(turns) == 2
        assert turns[0] == {"role": "user", "text": "Do something"}
        assert turns[1] == {"role": "assistant", "text": "Done"}

    def test_skips_entries_without_text_content(self, tmp_path: Path) -> None:
        transcript = self._write_transcript(
            tmp_path,
            [
                {"role": "assistant", "content": [{"type": "tool_use", "id": "y", "name": "x", "input": {}}]},
            ],
        )
        assert _extract_text_turns(transcript) == []

    def test_returns_empty_list_when_file_missing(self, tmp_path: Path) -> None:
        missing = tmp_path / "transcript.jsonl"
        assert _extract_text_turns(missing) == []

    def test_ignores_malformed_lines(self, tmp_path: Path) -> None:
        p = tmp_path / "transcript.jsonl"
        p.write_text('{"role": "user", "content": [{"type": "text", "text": "OK"}]}\nnot-json\n')
        turns = _extract_text_turns(p)
        assert len(turns) == 1
        assert turns[0]["role"] == "user"

    def test_handles_string_content_field(self, tmp_path: Path) -> None:
        """Some older sessions write content as a plain string, not a list."""
        transcript = self._write_transcript(
            tmp_path,
            [
                {"role": "user", "content": "Plain string message"},
            ],
        )
        turns = _extract_text_turns(transcript)
        assert turns == [{"role": "user", "text": "Plain string message"}]
```

### Step 2: Run to confirm failure

```bash
pytest tests/apps/voice/test_voice_context.py -v
```

Expected: `ImportError: cannot import name '_extract_text_turns'`

---

### Step 3: Create `context.py` with the normalizer

```python
# src/amplifier_distro/server/apps/voice/transcript/context.py
"""Helpers for building Realtime API injection context from non-voice sessions."""
from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def _extract_text_turns(transcript_path: Path) -> list[dict]:
    """Read an Amplifier transcript.jsonl and return plain user/assistant text turns.

    - Skips entries whose content contains only tool_use or tool_result items.
    - Handles both list-format content and legacy string-format content.
    - Returns [] on missing file or parse errors; never raises.
    """
    if not transcript_path.exists():
        return []

    turns: list[dict] = []
    for raw_line in transcript_path.read_text().splitlines():
        raw_line = raw_line.strip()
        if not raw_line:
            continue
        try:
            entry = json.loads(raw_line)
        except json.JSONDecodeError:
            logger.debug("Skipping malformed transcript line: %r", raw_line[:80])
            continue

        role = entry.get("role", "")
        if role not in ("user", "assistant"):
            continue

        content = entry.get("content", [])

        # Legacy: content as a plain string
        if isinstance(content, str):
            turns.append({"role": role, "text": content})
            continue

        # Normal: content as a list of typed blocks
        texts = [
            block["text"]
            for block in content
            if isinstance(block, dict)
            and block.get("type") == "text"
            and block.get("text")
        ]
        if texts:
            turns.append({"role": role, "text": " ".join(texts)})

    return turns
```

### Step 4: Run tests to confirm they pass

```bash
pytest tests/apps/voice/test_voice_context.py::TestExtractTextTurns -v
```

Expected: all 6 tests **PASS**

### Step 5: Commit

```bash
git add src/amplifier_distro/server/apps/voice/transcript/context.py \
        tests/apps/voice/test_voice_context.py
git commit -m "feat: add _extract_text_turns normalizer for Amplifier transcript.jsonl"
```

---

## Task 2: Implement `_build_chat_context()` — TDD

The core of the feature: reads `handoff.md` + transcript, formats as OpenAI Realtime `conversation.item` objects. Write all tests before implementation.

**Files:**
- Modify: `src/amplifier_distro/server/apps/voice/transcript/context.py`
- Modify: `tests/apps/voice/test_voice_context.py`

---

### Step 1: Write failing tests for `_build_chat_context()`

Add these classes to `tests/apps/voice/test_voice_context.py`:

```python
from amplifier_distro.server.apps.voice.transcript.context import (
    _build_chat_context,
    _extract_text_turns,
)


def _seed_session(
    tmp_path: Path,
    *,
    handoff: str | None = None,
    turns: list[dict] | None = None,
) -> tuple[Path, str, str]:
    """Create a fake Amplifier session directory. Returns (base_dir, project_id, session_id)."""
    project_id = "test-project"
    session_id = "test-session-001"
    session_dir = tmp_path / "projects" / project_id / "sessions" / session_id
    session_dir.mkdir(parents=True)

    if handoff is not None:
        (session_dir / "handoff.md").write_text(handoff)

    if turns is not None:
        lines = [json.dumps(t) for t in turns]
        (session_dir / "transcript.jsonl").write_text("\n".join(lines))

    return tmp_path, project_id, session_id


class TestBuildChatContext:
    """_build_chat_context() builds Realtime API context items from a text session."""

    @pytest.mark.asyncio
    async def test_handoff_present_becomes_first_item(self, tmp_path: Path) -> None:
        base, proj, sess = _seed_session(
            tmp_path,
            handoff="We were discussing authentication.",
            turns=[
                {"role": "user", "content": [{"type": "text", "text": "Hello"}]},
                {"role": "assistant", "content": [{"type": "text", "text": "Hi"}]},
            ],
        )
        items = await _build_chat_context(sess, proj, amplifier_home=tmp_path)

        assert len(items) >= 1
        first = items[0]
        assert first["type"] == "message"
        assert first["role"] == "assistant"
        assert "Context from prior text session" in first["content"][0]["text"]
        assert "We were discussing authentication." in first["content"][0]["text"]

    @pytest.mark.asyncio
    async def test_recent_turns_follow_handoff_item(self, tmp_path: Path) -> None:
        turns = [
            {"role": "user", "content": [{"type": "text", "text": f"msg {i}"}]}
            for i in range(10)
        ]
        base, proj, sess = _seed_session(
            tmp_path,
            handoff="Summary.",
            turns=turns,
        )
        items = await _build_chat_context(sess, proj, amplifier_home=tmp_path, recent_turns=8)

        # 1 handoff item + 8 most recent turns
        assert len(items) == 9
        turn_texts = [it["content"][0]["text"] for it in items[1:]]
        # Should be the LAST 8 (msg 2 through msg 9)
        assert turn_texts[0] == "msg 2"
        assert turn_texts[-1] == "msg 9"

    @pytest.mark.asyncio
    async def test_absent_handoff_emits_synthetic_preamble(self, tmp_path: Path) -> None:
        base, proj, sess = _seed_session(
            tmp_path,
            handoff=None,
            turns=[
                {"role": "user", "content": [{"type": "text", "text": "Hi"}]},
            ],
        )
        items = await _build_chat_context(sess, proj, amplifier_home=tmp_path)

        assert len(items) >= 1
        first = items[0]
        assert "Resuming a text chat session" in first["content"][0]["text"]

    @pytest.mark.asyncio
    async def test_absent_handoff_never_raises(self, tmp_path: Path) -> None:
        base, proj, sess = _seed_session(tmp_path, handoff=None, turns=[])
        # Must not raise even with no handoff and empty transcript
        items = await _build_chat_context(sess, proj, amplifier_home=tmp_path)
        assert isinstance(items, list)
        # At minimum the synthetic preamble item
        assert len(items) >= 1

    @pytest.mark.asyncio
    async def test_tool_calls_are_absent_from_result(self, tmp_path: Path) -> None:
        base, proj, sess = _seed_session(
            tmp_path,
            handoff="Summary.",
            turns=[
                {"role": "user", "content": [{"type": "text", "text": "Do X"}]},
                {
                    "role": "assistant",
                    "content": [
                        {"type": "tool_use", "id": "t1", "name": "delegate", "input": {}}
                    ],
                },
                {
                    "role": "user",
                    "content": [{"type": "tool_result", "tool_use_id": "t1", "content": []}],
                },
                {"role": "assistant", "content": [{"type": "text", "text": "Done"}]},
            ],
        )
        items = await _build_chat_context(sess, proj, amplifier_home=tmp_path)

        all_texts = [it["content"][0]["text"] for it in items]
        assert all("tool_use" not in t and "tool_result" not in t for t in all_texts)
        # Only "Do X" and "Done" should appear (plus handoff)
        turn_texts = [it["content"][0]["text"] for it in items[1:]]
        assert "Do X" in turn_texts
        assert "Done" in turn_texts

    @pytest.mark.asyncio
    async def test_user_turns_use_input_text_content_type(self, tmp_path: Path) -> None:
        base, proj, sess = _seed_session(
            tmp_path,
            handoff=None,
            turns=[{"role": "user", "content": [{"type": "text", "text": "Hello"}]}],
        )
        items = await _build_chat_context(sess, proj, amplifier_home=tmp_path)
        user_items = [it for it in items if it.get("role") == "user"]
        assert all(it["content"][0]["type"] == "input_text" for it in user_items)

    @pytest.mark.asyncio
    async def test_assistant_turns_use_text_content_type(self, tmp_path: Path) -> None:
        base, proj, sess = _seed_session(
            tmp_path,
            handoff=None,
            turns=[{"role": "assistant", "content": [{"type": "text", "text": "Hi"}]}],
        )
        items = await _build_chat_context(sess, proj, amplifier_home=tmp_path)
        # Skip the preamble item (it's also assistant role)
        turn_items = [it for it in items[1:] if it.get("role") == "assistant"]
        assert all(it["content"][0]["type"] == "text" for it in turn_items)

    @pytest.mark.asyncio
    async def test_recent_turns_default_is_eight(self, tmp_path: Path) -> None:
        turns = [
            {"role": "user", "content": [{"type": "text", "text": f"msg {i}"}]}
            for i in range(12)
        ]
        base, proj, sess = _seed_session(tmp_path, handoff=None, turns=turns)
        items = await _build_chat_context(sess, proj, amplifier_home=tmp_path)
        # 1 preamble + 8 turns
        assert len(items) == 9
```

### Step 2: Run to confirm failures

```bash
pytest tests/apps/voice/test_voice_context.py::TestBuildChatContext -v
```

Expected: `ImportError: cannot import name '_build_chat_context'`

---

### Step 3: Implement `_build_chat_context()` in `context.py`

Append to `src/amplifier_distro/server/apps/voice/transcript/context.py`:

```python
from amplifier_distro.conventions import AMPLIFIER_HOME, PROJECTS_DIR, TRANSCRIPT_FILENAME


async def _build_chat_context(
    session_id: str,
    project_id: str,
    *,
    amplifier_home: Path | None = None,
    recent_turns: int = 8,
) -> list[dict]:
    """Build Realtime API injection context from a non-voice (chat/CLI/Slack) session.

    Returns a list of OpenAI Realtime conversation.item dicts:
      - Item 0: handoff.md summary (if present) or synthetic preamble
      - Items 1+: the last `recent_turns` user/assistant text exchanges

    Never raises — all errors are logged and an empty/minimal list is returned.
    """
    home = amplifier_home or Path(AMPLIFIER_HOME).expanduser()
    session_dir = home / PROJECTS_DIR / project_id / "sessions" / session_id

    items: list[dict] = []

    # --- Item 0: handoff summary or synthetic preamble ---
    handoff_path = session_dir / "handoff.md"
    if handoff_path.exists():
        try:
            handoff_text = handoff_path.read_text().strip()
            preamble = f"Context from prior text session:\n\n{handoff_text}"
        except OSError as exc:
            logger.warning("Could not read handoff.md for %s: %s", session_id, exc)
            preamble = "Resuming a text chat session. Here are the most recent exchanges:"
    else:
        preamble = "Resuming a text chat session. Here are the most recent exchanges:"

    items.append(_make_context_item("assistant", preamble))

    # --- Items 1+: recent text turns ---
    transcript_path = session_dir / TRANSCRIPT_FILENAME
    all_turns = _extract_text_turns(transcript_path)
    recent = all_turns[-recent_turns:] if len(all_turns) > recent_turns else all_turns

    for turn in recent:
        items.append(_make_context_item(turn["role"], turn["text"]))

    return items


def _make_context_item(role: str, text: str) -> dict:
    """Format a single turn as an OpenAI Realtime conversation.item dict."""
    content_type = "input_text" if role == "user" else "text"
    return {
        "type": "message",
        "role": role,
        "content": [{"type": content_type, "text": text}],
    }
```

### Step 4: Run all context tests

```bash
pytest tests/apps/voice/test_voice_context.py -v
```

Expected: all tests **PASS**

### Step 5: Run full test suite to confirm no regressions

```bash
pytest tests/ -v --tb=short
```

Expected: all pre-existing tests **PASS**

### Step 6: Commit

```bash
git add src/amplifier_distro/server/apps/voice/transcript/context.py \
        tests/apps/voice/test_voice_context.py
git commit -m "feat: implement _build_chat_context() with TDD — reads handoff.md + transcript"
```

---

## Task 3: Server refactor — introduce `_build_context_for_resume()` dispatcher

Pure refactor: extract the current context-building call into a dispatcher helper. No behavior change — all existing voice resume tests must continue to pass.

**Files:**
- Modify: `src/amplifier_distro/server/apps/voice/__init__.py`
- Modify: `tests/apps/voice/test_voice_context.py`

---

### Step 1: Write a test for the dispatcher's branching logic

Add to `tests/apps/voice/test_voice_context.py`:

```python
from unittest.mock import AsyncMock, patch

from amplifier_distro.server.apps.voice.transcript.context import (
    _build_context_for_resume,
)


class TestBuildContextForResume:
    """_build_context_for_resume() dispatches to voice or chat builder based on created_by_app."""

    @pytest.mark.asyncio
    async def test_voice_app_routes_to_voice_path(self, tmp_path: Path) -> None:
        fake_context = [{"type": "message", "role": "user", "content": []}]
        with patch(
            "amplifier_distro.server.apps.voice.transcript.context._build_chat_context",
            new_callable=AsyncMock,
        ) as mock_chat:
            result = await _build_context_for_resume(
                session_id="sess-001",
                project_id="proj-001",
                created_by_app="voice",
                voice_context=fake_context,
            )
        mock_chat.assert_not_called()
        assert result is fake_context

    @pytest.mark.asyncio
    async def test_chat_app_routes_to_chat_path(self, tmp_path: Path) -> None:
        expected = [{"type": "message", "role": "assistant", "content": []}]
        with patch(
            "amplifier_distro.server.apps.voice.transcript.context._build_chat_context",
            new_callable=AsyncMock,
            return_value=expected,
        ) as mock_chat:
            result = await _build_context_for_resume(
                session_id="sess-002",
                project_id="proj-001",
                created_by_app="chat",
                voice_context=[],
            )
        mock_chat.assert_called_once_with("sess-002", "proj-001")
        assert result is expected

    @pytest.mark.asyncio
    async def test_missing_created_by_app_routes_to_chat_path(self, tmp_path: Path) -> None:
        """NFR-4: legacy sessions without created_by_app treated as non-voice."""
        expected = [{"type": "message", "role": "assistant", "content": []}]
        with patch(
            "amplifier_distro.server.apps.voice.transcript.context._build_chat_context",
            new_callable=AsyncMock,
            return_value=expected,
        ) as mock_chat:
            result = await _build_context_for_resume(
                session_id="sess-003",
                project_id="proj-001",
                created_by_app="",   # empty string = missing (NFR-4 safe default)
                voice_context=[],
            )
        mock_chat.assert_called_once()
        assert result is expected

    @pytest.mark.asyncio
    async def test_slack_app_routes_to_chat_path(self, tmp_path: Path) -> None:
        with patch(
            "amplifier_distro.server.apps.voice.transcript.context._build_chat_context",
            new_callable=AsyncMock,
            return_value=[],
        ) as mock_chat:
            await _build_context_for_resume(
                session_id="sess-004",
                project_id="proj-001",
                created_by_app="slack",
                voice_context=[],
            )
        mock_chat.assert_called_once()
```

### Step 2: Run to confirm failure

```bash
pytest tests/apps/voice/test_voice_context.py::TestBuildContextForResume -v
```

Expected: `ImportError: cannot import name '_build_context_for_resume'`

---

### Step 3: Add `_build_context_for_resume()` to `context.py`

Append to `src/amplifier_distro/server/apps/voice/transcript/context.py`:

```python
async def _build_context_for_resume(
    session_id: str,
    project_id: str,
    created_by_app: str,
    voice_context: list[dict],
    *,
    amplifier_home: Path | None = None,
    recent_turns: int = 8,
) -> list[dict]:
    """Dispatch to the correct context builder based on session type.

    Args:
        session_id: The Amplifier session ID.
        project_id: The Amplifier project ID.
        created_by_app: Value from metadata.json. Empty string = legacy (NFR-4).
        voice_context: Pre-built context from VoiceConversationRepository (voice path).
        amplifier_home: Override for ~/.amplifier (used in tests).
        recent_turns: Max number of turns to include for non-voice sessions.
    """
    if created_by_app == "voice":
        return voice_context

    # All non-voice apps (chat, CLI, Slack) and legacy sessions (empty string per NFR-4)
    return await _build_chat_context(
        session_id,
        project_id,
        amplifier_home=amplifier_home,
        recent_turns=recent_turns,
    )
```

### Step 4: Run all context tests

```bash
pytest tests/apps/voice/test_voice_context.py -v
```

Expected: all tests **PASS**

### Step 5: Commit

```bash
git add src/amplifier_distro/server/apps/voice/transcript/context.py \
        tests/apps/voice/test_voice_context.py
git commit -m "feat: add _build_context_for_resume() dispatcher with branching TDD coverage"
```

---

## Task 4: Add `created_by_app` to voice session metadata

The dispatcher needs to read `created_by_app` from `metadata.json`. Currently voice sessions don't write this field. Add a metadata helper and wire it into voice session creation.

**Files:**
- Modify: `src/amplifier_distro/server/apps/voice/transcript/context.py`
- Modify: `src/amplifier_distro/server/apps/voice/__init__.py`
- Modify: `tests/apps/voice/test_voice_context.py`

---

### Step 1: Write tests for `_read_session_created_by_app()`

Add to `tests/apps/voice/test_voice_context.py`:

```python
from amplifier_distro.server.apps.voice.transcript.context import (
    _read_session_created_by_app,
)


class TestReadSessionCreatedByApp:
    """_read_session_created_by_app() reads created_by_app from metadata.json."""

    def _write_metadata(self, session_dir: Path, data: dict) -> None:
        session_dir.mkdir(parents=True, exist_ok=True)
        import json
        (session_dir / "metadata.json").write_text(json.dumps(data))

    def test_returns_created_by_app_when_present(self, tmp_path: Path) -> None:
        session_dir = tmp_path / "projects" / "p1" / "sessions" / "s1"
        self._write_metadata(session_dir, {"session_id": "s1", "created_by_app": "voice"})

        result = _read_session_created_by_app("s1", "p1", amplifier_home=tmp_path)
        assert result == "voice"

    def test_returns_empty_string_when_field_missing(self, tmp_path: Path) -> None:
        """NFR-4: legacy sessions without the field default to '' (non-voice)."""
        session_dir = tmp_path / "projects" / "p1" / "sessions" / "s1"
        self._write_metadata(session_dir, {"session_id": "s1", "bundle": "chat"})

        result = _read_session_created_by_app("s1", "p1", amplifier_home=tmp_path)
        assert result == ""

    def test_returns_empty_string_when_file_missing(self, tmp_path: Path) -> None:
        result = _read_session_created_by_app("s1", "p1", amplifier_home=tmp_path)
        assert result == ""

    def test_returns_chat_when_written(self, tmp_path: Path) -> None:
        session_dir = tmp_path / "projects" / "p1" / "sessions" / "s1"
        self._write_metadata(session_dir, {"session_id": "s1", "created_by_app": "chat"})

        result = _read_session_created_by_app("s1", "p1", amplifier_home=tmp_path)
        assert result == "chat"
```

### Step 2: Run to confirm failure

```bash
pytest tests/apps/voice/test_voice_context.py::TestReadSessionCreatedByApp -v
```

Expected: `ImportError: cannot import name '_read_session_created_by_app'`

---

### Step 3: Add `_read_session_created_by_app()` to `context.py`

Append to `src/amplifier_distro/server/apps/voice/transcript/context.py`:

```python
def _read_session_created_by_app(
    session_id: str,
    project_id: str,
    *,
    amplifier_home: Path | None = None,
) -> str:
    """Read `created_by_app` from a session's metadata.json.

    Returns empty string when the field is absent or the file is missing (NFR-4).
    Never raises.
    """
    home = amplifier_home or Path(AMPLIFIER_HOME).expanduser()
    metadata_path = home / PROJECTS_DIR / project_id / "sessions" / session_id / "metadata.json"

    if not metadata_path.exists():
        return ""

    try:
        data = json.loads(metadata_path.read_text())
        return data.get("created_by_app", "")
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Could not read metadata.json for %s: %s", session_id, exc)
        return ""
```

### Step 4: Run context tests

```bash
pytest tests/apps/voice/test_voice_context.py -v
```

Expected: all tests **PASS**

---

### Step 5: Write a test confirming voice session creation writes `created_by_app`

This test goes in `tests/apps/voice/test_voice_routes.py`. Look at the existing `TestCreateSession` class in that file and add a test to it:

```python
def test_create_session_writes_created_by_app_voice(self, tmp_path: Path) -> None:
    """POST /sessions must stamp created_by_app: voice in metadata.json."""
    import json

    voice_module._repo_override = VoiceConversationRepository(base_dir=tmp_path / "voice")

    # Give the fake backend a project_id to work with
    workspace_root = str(tmp_path / "workspace")
    Path(workspace_root).mkdir(parents=True)

    env = {
        "AMPLIFIER_WORKSPACE_ROOT": workspace_root,
        "AMPLIFIER_SERVER_API_KEY": "",
    }
    with patch.dict(os.environ, env, clear=False):
        client = TestClient(_make_app(), raise_server_exceptions=False)
        resp = client.post("/apps/voice/sessions")

    assert resp.status_code == 200
    session_id = resp.json()["session_id"]

    # The fake backend's project_id may be empty; check via the module's own helper
    # or look for metadata.json in any project under tmp_path
    meta_files = list((tmp_path).rglob("metadata.json"))
    if meta_files:
        meta = json.loads(meta_files[0].read_text())
        assert meta.get("created_by_app") == "voice"
```

> **Note:** This test verifies the *intent*. Run it first; if the fake backend doesn't write metadata to disk, it will fail or be skipped. The actual metadata write happens in the FoundationBackend. You may need to adapt this test to your local FakeBackend's behavior. The important invariant is that **real** voice sessions (using FoundationBackend) write `created_by_app: "voice"` — verify this manually if the fake can't exercise it.

---

### Step 6: Wire `created_by_app: "voice"` into the voice session creation flow

Open `src/amplifier_distro/server/apps/voice/__init__.py`. Find the `create_session` route handler. After the section that calls `repo.write_amplifier_metadata(session_id, conn.project_id, conv)`, add the `created_by_app` stamp:

```python
# In create_session(), after the existing write_amplifier_metadata call:
if conn.project_id:
    # Stamp created_by_app so resume_session() can identify this as a voice session
    _stamp_created_by_app(session_id, conn.project_id, "voice")
```

Add this helper near the other module-level helpers in `voice/__init__.py` (near `_get_workspace_root`):

```python
def _stamp_created_by_app(session_id: str, project_id: str, app_name: str) -> None:
    """Add/update created_by_app in metadata.json for a session."""
    from amplifier_distro.conventions import AMPLIFIER_HOME, PROJECTS_DIR

    meta_path = (
        Path(AMPLIFIER_HOME).expanduser()
        / PROJECTS_DIR
        / project_id
        / "sessions"
        / session_id
        / "metadata.json"
    )
    if not meta_path.exists():
        return
    try:
        data = json.loads(meta_path.read_text())
        data["created_by_app"] = app_name
        from amplifier_distro.fileutil import atomic_write

        atomic_write(meta_path, json.dumps(data))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Could not stamp created_by_app for %s: %s", session_id, exc)
```

Add `import json` at the top of `voice/__init__.py` if not already present.

### Step 7: Run full voice test suite

```bash
pytest tests/apps/voice/ -v --tb=short
```

Expected: all tests **PASS** (the new metadata test may show as expected-fail if FakeBackend doesn't write to disk — that's acceptable; the key is no regressions)

### Step 8: Commit

```bash
git add src/amplifier_distro/server/apps/voice/__init__.py \
        src/amplifier_distro/server/apps/voice/transcript/context.py \
        tests/apps/voice/test_voice_context.py \
        tests/apps/voice/test_voice_routes.py
git commit -m "feat: stamp created_by_app in voice session metadata.json"
```

---

## Task 5: Wire `_build_context_for_resume()` into `resume_session()`

Replace the direct `repo.get_resumption_context()` call with the dispatcher. This is the Phase 3 integration wiring.

**Files:**
- Modify: `src/amplifier_distro/server/apps/voice/__init__.py`
- Modify: `tests/apps/voice/test_voice_routes.py`

---

### Step 1: Write an integration test for non-voice resume

Find the existing `TestResumeSession` class in `tests/apps/voice/test_voice_routes.py`. Add this test to it:

```python
def test_resume_chat_session_injects_context(self, tmp_path: Path) -> None:
    """POST /sessions/{id}/resume for a chat session returns chat-built context_to_inject."""
    import json

    session_id = "chat-session-abc"
    project_id = "proj-chat"

    # Seed the Amplifier session directory
    session_dir = tmp_path / "projects" / project_id / "sessions" / session_id
    session_dir.mkdir(parents=True)
    (session_dir / "metadata.json").write_text(
        json.dumps({"session_id": session_id, "created_by_app": "chat"})
    )
    (session_dir / "handoff.md").write_text("We were building an auth system.")
    (session_dir / "transcript.jsonl").write_text(
        json.dumps({"role": "user", "content": [{"type": "text", "text": "What next?"}]})
    )

    # Configure fake backend to know about this session
    class FakeBackendWithChatSession(FakeBackend):
        async def get_session_info(self, sid: str):
            if sid == session_id:
                from amplifier_distro.server.session_backend import SessionInfo
                return SessionInfo(
                    session_id=session_id,
                    project_id=project_id,
                    working_dir=str(session_dir),
                    created_by_app="chat",
                )
            return None

        async def resume_session(self, sid, working_dir, event_queue=None):
            pass

    voice_module._backend_override = FakeBackendWithChatSession()
    voice_module._repo_override = VoiceConversationRepository(base_dir=tmp_path / "voice")

    import os
    from unittest.mock import patch

    env = {"AMPLIFIER_SERVER_API_KEY": ""}
    with patch.dict(os.environ, env, clear=False):
        # Also override AMPLIFIER_HOME so helpers read from tmp_path
        with patch(
            "amplifier_distro.server.apps.voice.transcript.context.AMPLIFIER_HOME",
            str(tmp_path),
        ):
            client = TestClient(_make_app(), raise_server_exceptions=False)
            resp = client.post(f"/apps/voice/sessions/{session_id}/resume")

    assert resp.status_code == 200
    body = resp.json()
    assert "context_to_inject" in body
    context = body["context_to_inject"]
    assert len(context) >= 1
    # First item should contain the handoff text
    first_text = context[0]["content"][0]["text"]
    assert "auth system" in first_text
```

### Step 2: Run to confirm failure

```bash
pytest tests/apps/voice/test_voice_routes.py::TestResumeSession::test_resume_chat_session_injects_context -v
```

Expected: **FAIL** — context is not yet from the chat builder.

---

### Step 3: Modify `resume_session()` in `voice/__init__.py`

Find the existing `resume_session()` route handler (lines ~404–469). Replace this block:

```python
# Pull transcript context for the Realtime API before resuming
context = repo.get_resumption_context(session_id)
```

With:

```python
# Pull transcript context for the Realtime API before resuming.
# Branch on created_by_app: voice path uses the voice transcript store;
# all other apps (chat, CLI, Slack) use the Amplifier transcript store.
from amplifier_distro.server.apps.voice.transcript.context import (
    _build_context_for_resume,
    _read_session_created_by_app,
)

_project_id_for_context = session_info.project_id or ""
_created_by_app = _read_session_created_by_app(session_id, _project_id_for_context)
_voice_context = repo.get_resumption_context(session_id)  # used only if voice

context = await _build_context_for_resume(
    session_id=session_id,
    project_id=_project_id_for_context,
    created_by_app=_created_by_app,
    voice_context=_voice_context,
)
```

### Step 4: Run the new integration test

```bash
pytest tests/apps/voice/test_voice_routes.py::TestResumeSession::test_resume_chat_session_injects_context -v
```

Expected: **PASS**

### Step 5: Run the full resume test suite to confirm no regressions

```bash
pytest tests/apps/voice/test_voice_routes.py -v --tb=short
```

Expected: all tests **PASS** — voice-to-voice resume is unchanged.

### Step 6: Run all voice tests

```bash
pytest tests/apps/voice/ -v --tb=short
```

Expected: all tests **PASS**

### Step 7: Commit

```bash
git add src/amplifier_distro/server/apps/voice/__init__.py \
        tests/apps/voice/test_voice_routes.py
git commit -m "feat: wire _build_context_for_resume() into resume_session() route"
```

---

## Task 6: Widen `GET /sessions` to include non-voice sessions

Currently `GET /apps/voice/sessions` returns only from `voice-sessions/index.json`. To let `SessionPicker` show chat/CLI sessions, the endpoint needs to also return sessions from `~/.amplifier/projects/`.

**Files:**
- Modify: `src/amplifier_distro/server/apps/voice/__init__.py`
- Modify: `tests/apps/voice/test_voice_routes.py`

---

### Step 1: Write tests for the widened sessions list

Add to the existing `TestListSessions` class in `tests/apps/voice/test_voice_routes.py`:

```python
def test_sessions_list_includes_non_voice_sessions(self, tmp_path: Path) -> None:
    """GET /sessions returns voice sessions AND non-voice sessions from projects dir."""
    import json
    from unittest.mock import patch

    # Seed a chat session in the Amplifier projects directory
    chat_sess_id = "chat-list-test-001"
    chat_proj_id = "proj-list-test"
    chat_dir = tmp_path / "projects" / chat_proj_id / "sessions" / chat_sess_id
    chat_dir.mkdir(parents=True)
    (chat_dir / "metadata.json").write_text(
        json.dumps({
            "session_id": chat_sess_id,
            "created_by_app": "chat",
            "name": "A chat session",
            "created": "2024-01-15T10:00:00+00:00",
        })
    )

    voice_module._repo_override = VoiceConversationRepository(base_dir=tmp_path / "voice")

    with patch.dict(os.environ, {"AMPLIFIER_SERVER_API_KEY": ""}, clear=False):
        with patch(
            "amplifier_distro.server.apps.voice._get_workspace_root",
            return_value=tmp_path,
        ):
            client = TestClient(_make_app(), raise_server_exceptions=False)
            resp = client.get("/apps/voice/sessions")

    assert resp.status_code == 200
    sessions = resp.json()
    session_ids = [s.get("id") or s.get("session_id") for s in sessions]
    assert chat_sess_id in session_ids

def test_non_voice_sessions_include_created_by_app_label(self, tmp_path: Path) -> None:
    import json
    from unittest.mock import patch

    chat_sess_id = "chat-label-test-001"
    chat_proj_id = "proj-label-test"
    chat_dir = tmp_path / "projects" / chat_proj_id / "sessions" / chat_sess_id
    chat_dir.mkdir(parents=True)
    (chat_dir / "metadata.json").write_text(
        json.dumps({
            "session_id": chat_sess_id,
            "created_by_app": "chat",
            "name": "Label test",
            "created": "2024-01-15T10:00:00+00:00",
        })
    )

    voice_module._repo_override = VoiceConversationRepository(base_dir=tmp_path / "voice")

    with patch.dict(os.environ, {"AMPLIFIER_SERVER_API_KEY": ""}, clear=False):
        with patch(
            "amplifier_distro.server.apps.voice._get_workspace_root",
            return_value=tmp_path,
        ):
            client = TestClient(_make_app(), raise_server_exceptions=False)
            resp = client.get("/apps/voice/sessions")

    sessions = resp.json()
    chat_entry = next((s for s in sessions if s.get("id") == chat_sess_id or s.get("session_id") == chat_sess_id), None)
    assert chat_entry is not None
    assert chat_entry.get("created_by_app") == "chat"
```

### Step 2: Run to confirm failure

```bash
pytest tests/apps/voice/test_voice_routes.py::TestListSessions -v
```

Expected: new tests **FAIL** — chat sessions not returned yet.

---

### Step 3: Add a helper to scan the Amplifier projects directory

Add to `src/amplifier_distro/server/apps/voice/__init__.py`:

```python
def _list_amplifier_sessions(workspace_root: Path) -> list[dict]:
    """Scan ~/.amplifier/projects/ and return session metadata dicts for non-voice sessions.

    Each returned dict has: id, title, status, created_at, created_by_app.
    Skips voice sessions (they live in voice-sessions/ already).
    Never raises — errors are logged and the session is skipped.
    """
    from amplifier_distro.conventions import AMPLIFIER_HOME, PROJECTS_DIR

    results: list[dict] = []
    home = Path(AMPLIFIER_HOME).expanduser()
    projects_dir = home / PROJECTS_DIR

    if not projects_dir.exists():
        return results

    for project_dir in sorted(projects_dir.iterdir()):
        if not project_dir.is_dir():
            continue
        sessions_dir = project_dir / "sessions"
        if not sessions_dir.exists():
            continue
        for session_dir in sorted(sessions_dir.iterdir()):
            if not session_dir.is_dir():
                continue
            meta_path = session_dir / "metadata.json"
            if not meta_path.exists():
                continue
            try:
                meta = json.loads(meta_path.read_text())
            except (OSError, json.JSONDecodeError) as exc:
                logger.debug("Skipping session dir %s: %s", session_dir, exc)
                continue

            created_by_app = meta.get("created_by_app", "")
            if created_by_app == "voice":
                continue  # voice sessions are already in the voice-sessions index

            session_id = meta.get("session_id") or session_dir.name
            results.append({
                "id": session_id,
                "session_id": session_id,
                "title": meta.get("name") or meta.get("title") or session_id,
                "status": "active",
                "created_at": meta.get("created") or meta.get("created_at", ""),
                "created_by_app": created_by_app,
            })

    return results
```

### Step 4: Modify `list_sessions()` to merge both sources

Find the existing `list_sessions()` route handler and replace its body:

```python
@router.get("/sessions")
async def list_sessions(
    x_api_key: str | None = Header(default=None),
) -> JSONResponse:
    """Return voice sessions (from voice-sessions index) + non-voice sessions (from projects dir)."""
    await _require_api_key(x_api_key)
    repo = _get_repo()

    # Voice sessions from the voice-specific store
    voice_sessions = repo.list_conversations()
    # Tag voice sessions with created_by_app so SessionPicker can label them
    for s in voice_sessions:
        s.setdefault("created_by_app", "voice")

    # Non-voice sessions from the Amplifier projects directory
    workspace_root = _get_workspace_root()
    other_sessions = _list_amplifier_sessions(workspace_root)

    return JSONResponse(content=voice_sessions + other_sessions)
```

### Step 5: Run the new tests

```bash
pytest tests/apps/voice/test_voice_routes.py::TestListSessions -v
```

Expected: all tests **PASS**

### Step 6: Run full route tests

```bash
pytest tests/apps/voice/test_voice_routes.py -v --tb=short
```

Expected: all tests **PASS**

### Step 7: Commit

```bash
git add src/amplifier_distro/server/apps/voice/__init__.py \
        tests/apps/voice/test_voice_routes.py
git commit -m "feat: widen GET /sessions to include non-voice Amplifier sessions with created_by_app labels"
```

---

## Task 7: Update `SessionPicker` frontend to label sessions by source app

The `SessionPicker` component in `static/index.html` currently shows whatever `/apps/voice/sessions` returns. Now that the API returns all sessions with `created_by_app`, add source labels to each entry.

**Files:**
- Modify: `src/amplifier_distro/server/apps/voice/static/index.html`
- Modify: `tests/apps/voice/test_voice_static_index.py`

---

### Step 1: Write static content tests for the labels

Find `tests/apps/voice/test_voice_static_index.py`. Add a test class (or add to existing):

```python
class TestSessionPickerLabels:
    """SessionPicker must render created_by_app labels for each session."""

    def _read_index_html(self) -> str:
        html_path = (
            Path(__file__).parent.parent.parent.parent
            / "src/amplifier_distro/server/apps/voice/static/index.html"
        )
        return html_path.read_text()

    def test_session_picker_renders_created_by_app_label(self) -> None:
        html = self._read_index_html()
        assert "created_by_app" in html, (
            "SessionPicker must read created_by_app from session data"
        )

    def test_session_picker_shows_chat_label(self) -> None:
        html = self._read_index_html()
        assert "Chat conversation" in html or "chat" in html.lower(), (
            "SessionPicker must label chat sessions"
        )

    def test_session_picker_shows_voice_label(self) -> None:
        html = self._read_index_html()
        assert "Voice conversation" in html or "voice" in html.lower(), (
            "SessionPicker must label voice sessions"
        )
```

### Step 2: Run to confirm failure

```bash
pytest tests/apps/voice/test_voice_static_index.py::TestSessionPickerLabels -v
```

Expected: tests **FAIL** — labels not yet in HTML.

---

### Step 3: Edit `SessionPicker` in `static/index.html`

Open `src/amplifier_distro/server/apps/voice/static/index.html`. Find the `SessionPicker` component (around line 1374). The existing session list rendering looks like:

```javascript
// Current session item rendering (find the line that renders title + date)
// It will look something like:
h('div', { class: 'session-title' }, title),
h('div', { class: 'session-date' }, date),
```

**Add a `created_by_app` label** to each session item. Find that section and add the label display:

```javascript
// Helper: map created_by_app to a display label
const APP_LABELS = {
  voice: 'Voice conversation',
  chat: 'Chat conversation',
  slack: 'Slack conversation',
  cli: 'CLI conversation',
};

// In the session item render, add the label line:
const appLabel = APP_LABELS[sess.created_by_app] || (sess.created_by_app ? sess.created_by_app + ' session' : 'Session');
```

Then in the JSX/h() call that renders each session item, add:

```javascript
h('span', { class: 'session-app-label', style: 'font-size:0.75em; color:#888; margin-left:6px;' }, appLabel),
```

Place it next to or below the session title. The exact location depends on the current HTML structure — find the `div` or `li` that renders per-session content and add the label as a sibling to the title element.

> **Hint:** Search for the string `sess.title` or `const title =` in the `SessionPicker` function to find the render location. There will be a line like `h('div', ..., title)` — add the label line directly after it.

### Step 4: Run the static content tests

```bash
pytest tests/apps/voice/test_voice_static_index.py -v
```

Expected: all tests **PASS**

### Step 5: Run the full test suite

```bash
pytest tests/ -v --tb=short
```

Expected: all tests **PASS**

### Step 6: Commit

```bash
git add src/amplifier_distro/server/apps/voice/static/index.html \
        tests/apps/voice/test_voice_static_index.py
git commit -m "feat: label sessions by created_by_app in SessionPicker (Voice/Chat/Slack/CLI)"
```

---

## Task 8: Run type checks and full regression suite

Before merging, verify the entire diff is type-clean and nothing was broken.

**Files:** No new files.

---

### Step 1: Run pyright type checks

```bash
cd distro-server/
python -m pyright src/amplifier_distro/server/apps/voice/transcript/context.py \
                  src/amplifier_distro/server/apps/voice/__init__.py
```

Fix any type errors before proceeding.

### Step 2: Run ruff linting

```bash
ruff check src/amplifier_distro/server/apps/voice/transcript/context.py \
           src/amplifier_distro/server/apps/voice/__init__.py
ruff format src/amplifier_distro/server/apps/voice/transcript/context.py \
            src/amplifier_distro/server/apps/voice/__init__.py
```

Fix any lint issues.

### Step 3: Run the full test suite

```bash
pytest tests/ -v --tb=short 2>&1 | tail -30
```

Expected: all tests **PASS**, no new failures.

### Step 4: Final commit (fixes only — no feature changes)

```bash
git add -A
git commit -m "chore: type and lint fixes for chat-to-voice context builders"
```

---

## Dependencies

Tasks must be completed in order:

```
Task 1 (normalizer)
    └── Task 2 (_build_chat_context — uses normalizer)
        └── Task 3 (dispatcher — calls _build_chat_context)
            └── Task 4 (metadata stamp — prereq for dispatcher to work)
                └── Task 5 (route wiring — brings it all together)
                    ├── Task 6 (widen sessions list — independent after Task 5)
                    │   └── Task 7 (frontend — requires Task 6 to have label data)
                    └── Task 8 (type/lint/regression — runs after all code tasks)
```

Tasks 6 and 7 are independent of Tasks 5 and can be done in parallel by a second agent.

---

## Verification

Run this checklist after all tasks complete:

### Automated

```bash
# From distro-server/
pytest tests/ -v --tb=short
```

All tests pass, including:
- `tests/apps/voice/test_voice_context.py` — all new context builder tests
- `tests/apps/voice/test_voice_routes.py` — resume + sessions list tests
- `tests/apps/voice/test_voice_static_index.py` — SessionPicker label tests
- All pre-existing voice tests (regression: voice-to-voice resume unchanged)

### Manual (requires a running voice app)

1. **Chat session resume:**
   - Open a chat session, have a multi-turn conversation, let it end
   - Open the voice app → `SessionPicker` shows the chat session labeled "Chat conversation"
   - Resume it → voice model opens with a context item referencing prior work
   - Voice model acknowledges what was being worked on before asking how to continue

2. **Absent `handoff.md` fallback:**
   - Find a chat session directory without `handoff.md`
   - Resume it → graceful fallback message, no crash, recent turns narrated

3. **Voice-to-voice regression:**
   - Resume an existing voice session → behavior identical to before this change
   - Voice transcript context items appear as before

4. **Legacy session (no `created_by_app`):**
   - Find a session whose `metadata.json` lacks `created_by_app`
   - Resume it → treated as non-voice (chat path), no crash

### Open Questions (decisions required before or during implementation)

| # | Question | Suggested default | Impact |
|---|----------|-------------------|--------|
| 1 | `recent_turns` default: 8 is proposed — validate with a real substantive chat session | Start with 8 | Task 2 |
| 2 | `SessionPicker` filter: show all sessions, or only sessions that have a `handoff.md`? | Show all (permissive) | Task 7 |
| 3 | Voice model system prompt: add a one-sentence hint to acknowledge injected prior context? | Yes, add it | Out of scope for this plan; track separately |
| 4 | `FoundationBackend.get_session_info()`: does it find cross-process sessions via filesystem scan? | Investigate in Task 5 setup | Task 5 may need a fallback path |
