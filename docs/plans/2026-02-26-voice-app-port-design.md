# Voice App Port Design

## Goal

Port the voice app from `amplifier-distro-ramparte` (`lean-experience-server` branch) into the `amplifier-distro` fork, replacing the existing ancestor voice app, and fix three known runtime bugs during the port.

## Background

The `amplifier-distro` fork currently tracks upstream `main` and has a voice app at `distro-server/src/amplifier_distro/server/apps/voice/` that is behind ramparte's version. The `amplifier-distro-ramparte` fork is ahead of upstream — it has fixed 6 runtime bugs and added meaningful features including proper resume behavior, transcript mirroring, periodic sync, and history injection. The destination voice app has three confirmed bugs: user transcription not appearing in the UI, assistant transcript not streaming (wrong event names), and tool results always returning a hardcoded response instead of the actual Amplifier output.

This port brings the improved voice app into `amplifier-distro` while fixing those known bugs on top. No server or backend changes are needed — `FoundationBackend` is identical in both repos.

## Approach

Create a new branch in `amplifier-distro`, replace the voice app directory wholesale with ramparte's version (with minor adaptations), then apply the three bug fixes on top. All changes are app-layer only — `session_backend.py` is shared infrastructure and is not touched. The Python namespace is identical in both repos (`amplifier_distro.*`), so no import path changes are needed. The only filesystem difference is that ramparte lives at `src/amplifier_distro/` while distro lives at `distro-server/src/amplifier_distro/` — this has no code impact.

## Architecture

The voice app is a self-contained Flask blueprint living entirely within `distro-server/src/amplifier_distro/server/apps/voice/`. It registers routes for session management, WebRTC signaling, tool execution, and transcript access. It depends on `FoundationBackend` from `session_backend.py` for all session operations. The frontend is a single `static/index.html` using Preact/HTM that connects via WebRTC data channel to the OpenAI Realtime API.

Nothing outside the `voice/` directory is touched.

## Components

### Section 1: Scope and Branch

- **Branch:** `feat/voice-app` created in `amplifier-distro`
- **What changes:** `distro-server/src/amplifier_distro/server/apps/voice/` — entire directory replaced
- **What doesn't change:** `session_backend.py`, `services.py`, `app.py`, `conventions.py`, and all other apps (`chat/`, `slack/`, `settings/`, `install_wizard/`, `routines/`)

### Section 2: File Copy Plan

**Source:** `amplifier-distro-ramparte/src/amplifier_distro/server/apps/voice/`  
**Destination:** `distro-server/src/amplifier_distro/server/apps/voice/`

**Straight copy — no changes needed:**

| File/Directory | Notes |
|---|---|
| `protocols/` | Entire directory: `event_streaming.py`, `voice_approval.py`, `voice_display.py` |
| `transcript/` | Entire directory: `models.py`, `repository.py` (adds `write_to_amplifier_transcript`, `write_amplifier_metadata`, auto-titling) |
| `realtime.py` | Near-identical; ramparte adds clarifying docstrings only |
| `translator.py` | Identical in both repos |
| `static/vendor.js` | Byte-for-byte identical (MD5 confirmed) |
| `static/connection-health.mjs` | Byte-for-byte identical (MD5 confirmed) |
| `static/connection-health.test.mjs` | Identical |

**Copy with awareness (replacing diverged files):**

| File | What changed in ramparte | Action |
|---|---|---|
| `connection.py` | Adds `project_id` tracking; removes broken `mark_disconnected()` call | Copy as-is |
| `__init__.py` | Correct `cancel_session` signature for distro backend; proper resume route; tool handling fixes | Copy as-is |
| `static/index.html` | Diverged significantly; ramparte's version is the base | Copy, then apply bug fixes (Section 3) |

**Not copied:**

- `voice.html` (fallback stub, never served) — leave as-is in distro

### Section 3: Bug Fixes

Applied after the file copy. All changes are in the voice app layer only.

**Fix 1 — User transcription not appearing**

Two coordinated changes:

1. **`realtime.py`:** Add `input_audio_transcription: {"model": "whisper-1"}` to the server-side session creation payload. The OpenAI Realtime API supports this at creation time, giving belt-and-suspenders alongside the `session.update` already sent in `dc.onopen`.

2. **`static/index.html`:** Add a handler for `input_audio_buffer.speech_started` that immediately creates an empty user bubble when the user starts speaking. When `conversation.item.input_audio_transcription.completed` fires, fill the bubble with the transcript text. This mirrors the two-step pattern from `amplifier-voice`, which has confirmed working transcription.

**Fix 2 — Assistant transcript not streaming**

**`static/index.html`:** Update the two assistant event names in the data channel handler. The correct names per the OpenAI Realtime API docs are `response.audio_transcript.delta` and `response.audio_transcript.done`. Ramparte incorrectly uses `response.output_audio_transcript.*`. The upstream distro already has the correct names — this fix brings the frontend in line with the API spec.

**Fix 3 — Tool results always return `{"result": "delegated"}`**

**`__init__.py`:** The `tools/execute` route calls `backend.execute()` and immediately returns a hardcoded response, so the model never hears what Amplifier actually did. Fix: await the actual result from `backend.send_message()` and return it to the model.

## Data Flow

1. User speaks → WebRTC audio stream → OpenAI Realtime API
2. `input_audio_buffer.speech_started` → frontend creates empty user bubble
3. `conversation.item.input_audio_transcription.completed` → frontend fills user bubble with transcript
4. `response.audio_transcript.delta` → frontend streams assistant text bubble in real-time
5. `response.audio_transcript.done` → assistant bubble finalized
6. Tool call event → frontend POSTs to `/apps/voice/api/tools/execute` → `backend.send_message()` → actual result returned to model

## Error Handling

No changes to existing error handling patterns. The voice app's existing error boundaries and WebRTC reconnection logic (in `connection.py` and `connection-health.mjs`) are carried over from ramparte as-is.

## Testing Strategy

Three passes, all hands-on (runtime voice app, no automated test suite):

**Pass 1 — Server health**

Start the server after the port. Confirm clean startup with no import errors. Hit `/apps/voice/api/status` — expect model and voice config returned. Catches any broken imports or path issues from the copy.

**Pass 2 — Core voice flow**

Open the UI, create a session, connect WebRTC. Speak a sentence. Verify:
- A user bubble appears with the spoken words
- The assistant responds with streaming text (not empty or silent)
- Browser console shows a `session.updated` log confirming `input_audio_transcription` is set to `whisper-1`

This single flow validates all three bug fixes together.

**Pass 3 — Tool round-trip**

Ask the assistant to do something that triggers a `delegate` tool call (e.g., "what's in my home directory"). Confirm the assistant speaks about what Amplifier actually returned rather than giving a generic non-answer.

**Also verify:** Create a session, disconnect, reconnect — confirm the resume flow restores the existing session rather than creating a new one.

## Open Questions

- **Does the `speech_started` approach fully resolve user transcription?** Pass 2 testing will confirm. If `input_audio_buffer.speech_started` alone is insufficient (e.g., the event fires but transcription still doesn't complete), the fallback is to also check whether `input_audio_transcription` in the session creation payload is required vs. just belt-and-suspenders. The reference implementation in `amplifier-voice` confirms the two-step bubble pattern works end-to-end; the question is whether distro's backend session setup needs the explicit `whisper-1` configuration at creation time or only via `session.update`.
