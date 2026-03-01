# Chat-to-Voice Session Handoff Design

## Overview

Enable the voice app to resume any Amplifier session — chat, CLI, or Slack — by
injecting summarized prior context into the OpenAI Realtime session at the moment
of resumption. Users who switched from a text-based session to voice should be
able to pick up their conversation without re-explaining context.

**What we're building:** A server-side context dispatcher and a widened
`SessionPicker` frontend that together let the voice app identify the type of a
session being resumed and inject the right context items into the Realtime API.

**Why it's needed:** Currently, `GET /apps/voice/sessions` only returns
voice-originated sessions. Users have no way to continue chat or CLI work by
voice. When they try to resume a voice session the app reads a voice-specific
transcript store — there is no path for non-voice transcripts.

---

## Requirements

### Functional

| # | Requirement |
|---|-------------|
| FR-1 | `SessionPicker` lists all Amplifier sessions (voice + chat + CLI + Slack), each labelled by source app |
| FR-2 | Resuming a chat/CLI/Slack session injects a context preamble (from `handoff.md` when present, or a synthetic fallback) as the first Realtime conversation item |
| FR-3 | The injected context includes up to 8 recent user/assistant text turns, tool-call entries stripped |
| FR-4 | Resuming a voice session continues to work exactly as before — no behaviour change |
| FR-5 | Sessions whose `metadata.json` lacks `created_by_app` are treated as non-voice (safe default for legacy sessions) |
| FR-6 | Voice sessions created after this change carry `created_by_app: "voice"` in `metadata.json` |
| FR-7 | Each Realtime context item uses the correct OpenAI content type: `input_text` for user turns, `text` for assistant turns |
| FR-8 | Absent `handoff.md` produces a synthetic preamble — the resume path never errors over a missing file |

### Non-Functional

| # | Constraint |
|---|------------|
| NFR-1 | No new infrastructure — reads files already written by Amplifier Foundation |
| NFR-2 | Additive-only changes to shared server code (`spawn_registration.py`, `FoundationBackend`) — zero impact on chat, Slack, or routines surfaces |
| NFR-3 | All new logic is fully covered by `pytest` unit tests; no new test frameworks |
| NFR-4 | The voice client (`handleResume()`) is not modified — it fires the same `POST /sessions/{id}/resume` regardless of session type |
| NFR-5 | `pyright` and `ruff` clean throughout |

---

## Design

### Architecture

```
SessionPicker (frontend)
      │  GET /apps/voice/sessions
      ▼
list_sessions()  ─── voice-sessions/index.json (existing)
                 ─── ~/.amplifier/projects/*/sessions/*/metadata.json (NEW)
                                                    │
                                               created_by_app label
                                               returned per entry

      │  POST /apps/voice/sessions/{id}/resume  (unchanged client call)
      ▼
resume_session()
      │
      ├─ _read_session_created_by_app(session_id, project_id)
      │       reads metadata.json → "voice" | "chat" | "slack" | ""
      │
      └─ _build_context_for_resume(session_id, project_id, created_by_app, voice_context)
              │
              ├─ created_by_app == "voice"  →  voice_context  (existing path, unchanged)
              │
              └─ anything else             →  _build_chat_context()
                                                  │
                                                  ├─ reads handoff.md → preamble item
                                                  ├─ reads transcript.jsonl → recent turns
                                                  └─ returns list[RealtimeItem]
```

### Session Type Discriminator

`metadata.json` is the Amplifier Foundation convention file written by every
surface on session creation. The `created_by_app` field is added to the voice
surface's creation path as part of this change. Non-voice surfaces already write
it. Legacy sessions (field absent) default to the non-voice path (NFR-5).

### Context Item Format

All injected items conform to the OpenAI Realtime API `conversation.item.create`
message format:

```json
{
  "type": "message",
  "role": "assistant" | "user",
  "content": [
    { "type": "text" | "input_text", "text": "..." }
  ]
}
```

User turns use `input_text`; assistant turns and the preamble use `text`.

---

## Components

### `_extract_text_turns(transcript_path: Path) → list[dict]`

**File:** `server/apps/voice/transcript/context.py`

Reads an Amplifier `transcript.jsonl` and returns plain `{role, text}` pairs.

- Skips entries containing only `tool_use` or `tool_result` content blocks
- Handles both list-format `content` and legacy plain-string `content`
- Returns `[]` on missing file or parse errors; never raises

### `_build_chat_context(session_id, project_id, *, amplifier_home, recent_turns=8) → list[dict]`

**File:** `server/apps/voice/transcript/context.py`

Builds the list of Realtime API context items for a non-voice session:

1. **Item 0 (preamble):** If `handoff.md` exists, its content is wrapped in
   `"Context from prior text session:\n\n{handoff_text}"`. If absent, emits
   `"Resuming a text chat session. Here are the most recent exchanges:"`.
2. **Items 1–N:** The last `recent_turns` text turns from `transcript.jsonl`,
   each formatted as a Realtime conversation item.

Never raises — all errors are logged and a minimal list (at least the preamble)
is always returned.

### `_build_context_for_resume(session_id, project_id, created_by_app, voice_context, ...) → list[dict]`

**File:** `server/apps/voice/transcript/context.py`

Dispatcher. Branches on `created_by_app`:

- `"voice"` → returns `voice_context` unchanged (existing path)
- anything else (including `""`) → calls `_build_chat_context()`

### `_read_session_created_by_app(session_id, project_id, ...) → str`

**File:** `server/apps/voice/transcript/context.py`

Reads `created_by_app` from `metadata.json`. Returns `""` when the field is
absent or the file is missing. Never raises.

### `_stamp_created_by_app(session_id, project_id, app_name) → None`

**File:** `server/apps/voice/__init__.py`

Writes/updates `created_by_app` in an existing `metadata.json` using an atomic
write. Called during voice session creation so new voice sessions are correctly
identified when resumed later. No-ops silently if `metadata.json` doesn't exist.

### `_list_amplifier_sessions(workspace_root: Path) → list[dict]`

**File:** `server/apps/voice/__init__.py`

Scans `~/.amplifier/projects/*/sessions/*/metadata.json` and returns session
summary dicts with keys: `id`, `session_id`, `title`, `status`, `created_at`,
`created_by_app`. Skips voice sessions (already in the voice index). Never
raises — errors are logged and the affected session is skipped.

### `list_sessions()` route (modified)

**File:** `server/apps/voice/__init__.py`

Merges voice sessions from `repo.list_conversations()` with non-voice sessions
from `_list_amplifier_sessions()`. Tags voice sessions with
`created_by_app: "voice"` via `setdefault` so existing entries are not
double-stamped.

### `resume_session()` route (modified)

**File:** `server/apps/voice/__init__.py`

Replaces the direct `repo.get_resumption_context(session_id)` call with the
dispatcher. New flow:

```python
created_by_app = _read_session_created_by_app(session_id, project_id)
voice_context  = repo.get_resumption_context(session_id)  # voice path only
context = await _build_context_for_resume(
    session_id, project_id, created_by_app, voice_context
)
```

### `SessionPicker` component (modified)

**File:** `server/apps/voice/static/index.html`

Adds a `created_by_app` label to each session list entry using a static map:

```javascript
const APP_LABELS = {
  voice: 'Voice conversation',
  chat:  'Chat conversation',
  slack: 'Slack conversation',
  cli:   'CLI conversation',
};
```

Displays as a small muted label alongside the session title.

---

## Data Flow

### Resuming a chat session

```
1. User opens SessionPicker
   → GET /apps/voice/sessions
   → Returns voice sessions + chat sessions (labelled "Chat conversation")

2. User selects a chat session
   → POST /apps/voice/sessions/{id}/resume  (client unchanged)

3. Server:
   a. _read_session_created_by_app()  →  "chat"
   b. repo.get_resumption_context()   →  [] (voice store has no entry)
   c. _build_context_for_resume()     →  dispatches to _build_chat_context()
   d. _build_chat_context():
      - reads handoff.md → preamble text
      - reads transcript.jsonl → last 8 text turns
      - returns list[RealtimeItem]
   e. response: { session_id, context_to_inject: [...items] }

4. Client injects items via conversation.item.create before response.create
   → Voice model greets user with prior context already loaded
```

### Resuming a voice session (unchanged)

```
1. GET /apps/voice/sessions → voice sessions labelled "Voice conversation"
2. POST /apps/voice/sessions/{id}/resume
3. _read_session_created_by_app() → "voice"
4. _build_context_for_resume() → returns voice_context unchanged
5. Client injects voice transcript items (same as before)
```

---

## Error Handling

| Scenario | Behaviour |
|----------|-----------|
| `handoff.md` missing | Synthetic preamble emitted; no error |
| `transcript.jsonl` missing or empty | Only preamble item returned; resume completes |
| `metadata.json` missing or malformed | `_read_session_created_by_app()` returns `""`; non-voice path taken |
| `_stamp_created_by_app()` write fails | Logged warning; session creation continues unaffected |
| `_list_amplifier_sessions()` scan error | Affected session skipped; remaining sessions returned |
| Legacy session (no `created_by_app`) | Treated as non-voice — safe, useful default |
| Very long transcript | `recent_turns=8` cap ensures Realtime token budget is bounded |

---

## Implementation Approach

Work proceeds in strict dependency order — each phase's tests must pass before
the next begins.

### Phase 1 — Transcript normalizer (isolated utility)

Create `server/apps/voice/transcript/context.py` with `_extract_text_turns()`.
Write and pass all unit tests before moving on. Pure Python, no I/O except the
file system — fast and isolated.

### Phase 2 — Chat context builder + dispatcher

Add `_build_chat_context()` and `_build_context_for_resume()` to `context.py`.
Add `_read_session_created_by_app()`. Full TDD: write all tests before
implementation. No server integration yet.

### Phase 3 — Voice route wiring

Stamp `created_by_app: "voice"` in voice session creation. Replace the
`get_resumption_context()` call in `resume_session()` with the dispatcher.
Wire in `_read_session_created_by_app()`. Run existing voice route tests as
regression check — the voice-to-voice path must be unchanged.

### Phase 4 — Sessions list + frontend (can run in parallel with Phase 3)

Add `_list_amplifier_sessions()`. Modify `list_sessions()` route to merge both
sources. Update `SessionPicker` to render `created_by_app` labels. Verify with
static content tests.

### Phase 5 — Type, lint, and full regression

Run `pyright` + `ruff` on all modified files. Run the complete test suite.
Manual verification checklist (see Testing Strategy).

---

## Testing Strategy

### Unit tests

**`tests/apps/voice/test_voice_context.py`** — all new context helpers:

| Class | Tests |
|-------|-------|
| `TestExtractTextTurns` | returns user+assistant turns; strips tool_use; skips non-text; missing file → []; malformed lines; legacy string content |
| `TestBuildChatContext` | handoff present → first item contains it; recent_turns truncation; absent handoff → synthetic preamble; no raises with empty inputs; tool calls stripped; correct content types (input_text vs text); default recent_turns=8 |
| `TestBuildContextForResume` | voice → returns voice_context unchanged; chat → calls _build_chat_context; empty created_by_app → chat path; slack → chat path |
| `TestReadSessionCreatedByApp` | returns field when present; returns "" when field missing; returns "" when file missing; reads "chat" correctly |

### Integration tests

**`tests/apps/voice/test_voice_routes.py`** — additions to existing classes:

| Test | Class |
|------|-------|
| `test_resume_chat_session_injects_context` | `TestResumeSession` — verifies handoff text appears in `context_to_inject` |
| `test_create_session_writes_created_by_app_voice` | `TestCreateSession` — verifies `metadata.json` is stamped |
| `test_sessions_list_includes_non_voice_sessions` | `TestListSessions` |
| `test_non_voice_sessions_include_created_by_app_label` | `TestListSessions` |

### Static content tests

**`tests/apps/voice/test_voice_static_index.py`** — `TestSessionPickerLabels`:

- `created_by_app` referenced in HTML
- `"Chat conversation"` label present
- `"Voice conversation"` label present

### Manual verification checklist (pre-merge)

1. **Chat resume:** Open SessionPicker → chat session appears labelled "Chat conversation" → resume → voice model acknowledges prior context
2. **Absent `handoff.md`:** Resume a chat session without `handoff.md` → graceful fallback message, no crash
3. **Voice regression:** Resume an existing voice session → identical to pre-change behaviour
4. **Legacy session:** Find a session without `created_by_app` → resume → treated as non-voice, no crash
5. **Empty transcript:** Resume a session with no turns → only preamble injected, model proceeds normally

### Regression gate

All pre-existing tests in `tests/apps/voice/` must pass unchanged. The new
`event_forwarder` parameter in `spawn_registration.py` (from the delegation
feedback feature) defaults to `None`; this change adds no further shared-code
mutations.

---

## Open Questions

| # | Question | Suggested default | Decided |
|---|----------|-------------------|---------|
| OQ-1 | `recent_turns` default: 8 turns proposed — is this sufficient for substantive chat sessions? | 8 | Pending |
| OQ-2 | `SessionPicker` filter: show all sessions, or only sessions with a `handoff.md`? | Show all (permissive) | Pending |
| OQ-3 | Voice model system prompt: add a hint to acknowledge injected prior context? | Yes, one sentence | Out of scope for this plan — track separately |
| OQ-4 | `FoundationBackend.get_session_info()`: does it find cross-process sessions via filesystem scan, or only sessions created in the current process? | Needs investigation in Phase 3 setup | Phase 3 may need a fallback read from `metadata.json` directly |
| OQ-5 | Cross-project sessions: if a user has sessions across multiple projects, should `SessionPicker` group by project? | Flat list for now | Pending |
