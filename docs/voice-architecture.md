# Voice App Architecture: Design Reference

**Date:** 2026-02-27  
**Status:** Working reference — not a permanent artifact  
**Scope:** `distro-server/src/amplifier_distro/server/apps/voice/`

---

## Why This Document Exists

The voice app diverges from the chat app in several visible ways. Some of those divergences are inherent to how real-time voice over WebRTC works. Some are refactor artifacts from abandoned prior paths. Without a document saying which is which, future work risks either "fixing" things that are correct by design or preserving things that are genuinely broken.

This document declares the intended architecture, explains the constraints that force certain patterns, and records the design decisions made during the 2026-02-27 review.

---

## The Fundamental Constraint: Server Is Not In the Audio Path

The voice app uses WebRTC-first architecture. Audio flows **directly** between the browser and OpenAI — the distro server never touches it:

```
Browser ←————————————————→ OpenAI Realtime API
         WebRTC (audio + data channel)

Browser → POST /sessions ——→ Distro Server
Browser → GET /session ——→ Distro Server → POST /client_secrets → OpenAI
Browser → GET /events ←—— SSE stream ←—— Distro Server (Amplifier events only)
```

This is the correct production architecture and is not negotiable. It means:

- The server never sees conversation turns as they happen
- Transcript persistence **must** be driven by the browser via `POST /sessions/{id}/transcript`
- Event streaming for Amplifier tool work is via SSE, not WebSocket
- One voice session at a time is an architectural reality of WebRTC, not a design oversight

These are not gaps. They are how this works.

---

## The Dual-Session Identity Model

Every voice interaction involves two distinct session concepts that must stay coupled:

| Session | Owner | Lifetime | Holds |
|---------|-------|----------|-------|
| **OpenAI Realtime session** | OpenAI | ~15–60 min (ephemeral WebRTC token) | Audio stream, transcription, voice model context |
| **Amplifier session** | Distro server / filesystem | Indefinite | Agent context, tool history, memory, transcript |

The `VoiceConversation` model (`transcript/models.py`) exists specifically to track this coupling. Its `status` field — `active`, `disconnected`, `ended` — is meaningful:

- `disconnected`: OpenAI Realtime session has dropped, but the Amplifier session is alive and resumable
- `ended`: Both sessions are terminated; cannot be resumed

The `voice-sessions/` directory at `~/.amplifier/voice-sessions/` holds voice-specific state (duration, reconnect history, disconnect events, OpenAI model/voice config) that has no place in the generic `~/.amplifier/projects/` schema. It is a declared extension, not a violation of the contract.

Voice sessions write stub transcripts to `~/.amplifier/projects/{project_id}/sessions/{session_id}/` so `session_history.py`'s `scan_sessions()` finds them — this is the cross-interface discoverability mechanism promised by the AMPLIFIER_HOME_CONTRACT.

---

## Declared Architecture: Voice as Pure Orchestrator

The voice model's role is **conversational orchestration only**. It does not perform tool work directly. It talks to the user, decides what to delegate, and narrates results.

The only tool the voice model exposes to OpenAI is `delegate`. This bridges to the Amplifier session via `backend.send_message()`, which runs the full agent loop including sub-agent spawning if needed.

```
User speaks
  → OpenAI Realtime model decides to call "delegate"
  → Browser: POST /tools/execute {name: "delegate", arguments: {instruction}}
  → Server: backend.send_message(session_id, instruction)
  → Amplifier session executes (with full tool access, sub-agent spawning, etc.)
  → Result returned to browser as function_call_output
  → Voice model narrates result
```

All reasoning, tool work, and delegation happen inside the Amplifier session. The voice model handles the conversation.

**Policy implications:**
- Child agents spawned from voice sessions should have `exclude_tools: ["delegate"]` — prevents recursive delegation chains where a sub-agent tries to spawn further sub-agents back through the voice bridge
- Approval for dangerous operations is handled via **conversational consent** (the voice model is instructed to ask before delegating), not interactive approval gates — you cannot pop a modal mid-voice-conversation
- The `cancel_current_task` tool is legitimate infrastructure (voice model can cancel a running Amplifier execution); `pause_replies`/`resume_replies` are browser-local VAD management

---

## What Belongs in the Voice App vs. What Belongs Elsewhere

### Inherent to the voice architecture — keep as-is

| Pattern | Why it's correct |
|---------|-----------------|
| Manual transcript sync via `POST /sessions/{id}/transcript` | Content never flows through server; browser has the transcript |
| `~/.amplifier/voice-sessions/` for voice-specific state | Duration, reconnect history, disconnect events have no generic schema equivalent |
| Single `_active_connection` global | One WebRTC session at a time is a protocol constraint |
| SSE for event streaming (not WebSocket) | Audio channel is WebRTC; events are unidirectional server→browser |
| Resume flow returns `context_to_inject` | Rehydrating a new Realtime session from Amplifier history has no chat equivalent |

### Refactor artifacts — clean up

| Pattern | What it is | Fix |
|---------|-----------|-----|
| `VOICE_TOOLS` dict in `__init__.py` | Wrong format, never reaches OpenAI; dead code from earlier approach | Remove |
| `translator.py` | Orphaned class, never instantiated | Remove |
| Duplicate `ConnectionHealthManager` in `index.html` | `.mjs` file exists but isn't imported | Remove inline duplicate, import from `.mjs` |
| `"spawn"` capability in `VoiceConnection.create()` | Naming drift; `session.spawn` is the correct key | Remove |
| Server-side dead paths for `pause_replies`/`resume_replies` | These are browser-local; server always returns `acknowledged` and does nothing | Remove |

---

## Resume Story

### Voice-to-voice reconnect (working today)

`POST /sessions/{id}/resume` fetches the voice transcript, converts it to OpenAI Realtime `conversation.item` format, and returns it as `context_to_inject`. The new WebRTC session is initialized with this history. The Amplifier session is the same — only the Realtime connection is new.

### Chat-to-voice (north star, not yet implemented)

The AMPLIFIER_HOME_CONTRACT promises: "Cross-interface — CLI session can be resumed in TUI, voice, or web chat." Chat-to-voice is the voice direction of this promise.

The mechanism is the same as voice-to-voice reconnect: resume the same Amplifier session, inject context into a new Realtime session. The difference is what context to inject.

**A chat session transcript is not the right context for voice.** It contains markdown, code blocks, long assistant responses, and tool outputs that the voice model cannot usefully narrate and that would consume most of the Realtime API's 128K context budget before the user speaks.

The right context unit for chat-to-voice is:
1. The session's `handoff.md` (auto-generated by `hooks-handoff` — concise summary of what was worked on and current state)
2. The last 5–10 turns for immediate conversational coherence
3. NOT the full transcript

**Design decisions that matter now (before implementation):**
- The resume route's context injection should be a function of "what kind of session is this" — `SessionInfo.created_by_app` is the discriminator; do not hardcode the assumption that we're always resuming a voice transcript
- `metadata.json`'s `created_by_app` field is load-bearing for this feature; do not remove or rename it
- The proactive reconnect wiring (needed now for session expiry) uses the same `handleResume` call that chat-to-voice will use — design it to accept session source as a parameter

---

## Known Gaps and Their Status

| Gap | Severity | Tracked In |
|-----|----------|-----------|
| Tool execution timing: `output_item.added` → `output_item.done` | P0 — can corrupt arguments silently | Issue #voice-api-timing |
| VAD format: beta flat path → GA nested path | P1 — pause/resume is a no-op today | Issue #voice-vad-format |
| Missing `conversation.item.truncate` on interruption | P1 — gradual context corruption | Issue #voice-interruption |
| Proactive reconnect not wired | P1 — sessions drop silently at expiry | Issue #voice-reconnect |
| Hook leak on reconnect | P2 — dead hooks accumulate | Issue #voice-hook-leak |
| Dead code not yet removed | P2 — maintenance burden | Issue #voice-dead-code |
| `exclude_tools: ["delegate"]` not enforced on child agents | P2 — recursive delegation possible | Issue #voice-orchestrator |
| `voice-sessions/` not documented in AMPLIFIER_HOME_CONTRACT | P3 — contract incomplete | Issue #voice-contract-doc |

---

## Relationship to `amplifier-voice` Standalone App

The standalone `amplifier-voice` app exists in the workspace as a reference implementation. It articulates the orchestrator model more explicitly (`REALTIME_TOOLS = {"delegate"}` as enforced policy, cancellation tracking via `_active_child_sessions`, conversational consent approval). We are not maintaining it.

The distro voice app should adopt the **principles** from the standalone app without adopting its implementation. Specifically: explicit `exclude_tools` enforcement, and documented orchestrator policy. The distro app's shared `FoundationBackend` and proper hook wiring make it the better long-term home for voice integration.
