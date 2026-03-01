# Voice Application Review: OpenAI Realtime API — Tool Usage & Delegation

**Date:** 2026-02-27  
**Scope:** `distro-server/src/amplifier_distro/server/apps/voice/`  
**Focus:** Tool usage correctness, delegation patterns, comparison to current Realtime API best practices

---

## Architecture Overview

The app uses a **WebRTC-first architecture** — the browser connects directly to OpenAI over WebRTC; the server brokers ephemeral tokens, manages Amplifier sessions, executes tool calls, and streams Amplifier events back to the browser via SSE. This is the correct production architecture (WebRTC client ↔ server WebSocket to OpenAI) and aligns with current best practice.

```
Browser                    Distro Server              OpenAI
  |                              |                       |
  |-- POST /sessions ----------->|                       |
  |-- GET /session ------------->|-- POST /client_secrets|
  |<-- {client_secret} ----------|<-- ephemeral token ---|
  |                              |                       |
  | [getUserMedia → RTCPeer → createDataChannel('oai-events')]
  |                              |                       |
  |-- POST /sdp (SDP offer) ---->|-- POST /calls (relay) |
  |<-- SDP answer ---------------|<-- SDP answer --------|
  |                              |                       |
  |<============= WebRTC audio + data channel ==========>|
  |                              |                       |
  | dc.onopen → session.update (VAD + tools)              |
```

---

## What's Correct

### Tool format (browser side)

On `dc.onopen`, tools are registered via `session.update` using the correct GA API format:

```js
{ type: 'function', name: 'delegate', description: '...', parameters: {...} }
```

The `type: 'function'` wrapper is required by the GA API (distinct from Chat Completions format which nests under a `function` key).

### Result submission sequence

After tool execution the browser correctly:

1. Sends `conversation.item.create` with `{ type: 'function_call_output', call_id, output }`
2. Sends `response.create` — but only if no response is already in progress

The `responseInProgress` guard and `pendingAnnouncements` queue are a good pattern that prevents triggering a new response turn while one is already running.

### Delegation pattern

The `delegate` tool is the right abstraction for a voice-to-Amplifier bridge:

```
User speaks
  → model decides to call "delegate"
  → response.output_item.added fires
  → browser POSTs /tools/execute {name: "delegate", arguments: {instruction: "..."}}
  → server: backend.send_message(session_id, instruction)
  → FoundationBackend._session_worker drains per-session FIFO queue
  → Amplifier session executes (including sub-agent spawning if needed)
  → result text returned synchronously to browser
  → browser sends function_call_output → model narrates result
```

The voice model acts as a natural language interface; actual work is delegated to the Amplifier backend. Sub-agent delegation also works correctly — `session.spawn` is registered in `spawn_registration.py` with belt-and-suspenders in `VoiceConnection.create()`, and child sessions share the same event queue so SSE streaming continues across delegated agents.

---

## Issues: Tool Usage

### P0 — Tool execution timing races argument streaming

**Files:** `static/index.html:1015`

Tools are executed when `response.output_item.added` fires with `item.type === 'function_call'`. Per the Realtime API event sequence, this event fires when the item is *created* — arguments stream in afterward:

```
response.output_item.added          ← item created, arguments may be empty/partial
response.function_call_arguments.delta  ← arguments streaming in
response.function_call_arguments.done   ← arguments complete ← correct execution point
```

Executing on `.added` means the `arguments` field may be an empty string or truncated JSON when the tool runs. This may appear to work if OpenAI happens to fully populate `arguments` before firing the event over the data channel, but this is not guaranteed and has been reported as inconsistent.

**Fix:** Move tool execution to `response.output_item.done` (fires per-item when complete, with full arguments), or accumulate deltas and act on `response.function_call_arguments.done`:

```js
// Option A: act on output_item.done (simplest)
case 'response.output_item.done':
  if (event.item.type === 'function_call') {
    await executeTool(event.item.name, JSON.parse(event.item.arguments), event.item.call_id);
  }
  break;

// Option B: accumulate deltas (more explicit)
case 'response.function_call_arguments.delta':
  pendingArgs[event.item_id] = (pendingArgs[event.item_id] || '') + event.delta;
  break;
case 'response.function_call_arguments.done':
  await executeTool(event.name, JSON.parse(pendingArgs[event.item_id]), event.call_id);
  delete pendingArgs[event.item_id];
  break;
```

---

### P1 — `pause_replies` VAD format is beta API, not GA

**File:** `static/index.html:1296-1312`

When entering pause mode, the browser sends:
```js
{ type: 'session.update', session: { turn_detection: { type: 'semantic_vad', ... } } }
```

The GA API format for turn detection is nested under `audio.input`:
```js
{ type: 'session.update', session: { audio: { input: { turn_detection: { type: 'server_vad', ... } } } } }
```

The flat `turn_detection` path was the beta API format. OpenAI silently ignores unknown fields, so `pause_replies` may be a complete no-op — VAD never actually changes. The `error` handler logs full event JSON to `console.error`, so this is debuggable: check the browser console for `session.updated` confirmation events when `pause_replies` fires to see whether VAD config actually changed.

**Fix:** Update the `enterPauseReplies`/`exitPauseReplies` `session.update` payloads to use the GA nested path.

---

### P1 — No `conversation.item.truncate` on user interruption

**Current behavior:** When the user interrupts a model response, `responseInProgress` is cleared but the model's partially-delivered audio is not truncated in the conversation history. This leaves the conversation context misaligned — the model thinks the user heard content they didn't.

**Best practice:** Send `conversation.item.truncate` when an interruption is detected (e.g., `input_audio_buffer.speech_started` fires while `responseInProgress` is true):

```js
case 'input_audio_buffer.speech_started':
  if (responseInProgress) {
    dc.send(JSON.stringify({
      type: 'conversation.item.truncate',
      item_id: currentOutputItemId,
      content_index: 0,
      audio_end_ms: Date.now() - responseStartTime,
    }));
  }
  break;
```

Without this, repeated interruptions will gradually corrupt the conversation context.

---

## Issues: Delegation

### P1 — Proactive reconnect is a TODO

**File:** `static/index.html:1592-1595`

`ConnectionHealthManager` correctly detects when session age > 55 minutes and fires `onProactiveReconnect`, but the callback is unimplemented:

```js
onProactiveReconnect: useCallback(() => {
    console.debug('[VoiceApp] Proactive reconnect triggered by session_limit warning');
    // TODO: implement proactive reconnect flow
}, []),
```

OpenAI Realtime sessions have a 15-minute hard limit. When the session expires mid-conversation the connection drops with no recovery. The health manager detection is correct — it needs the reconnect flow wired to call `resume_session()`.

---

### P2 — Hook leak on reconnect

**File:** `connection.py:136-138`

```python
# Note: _hook_unregister is not yet wired because backend.create_session()
# handles hook registration internally when event_queue is passed and does not
# return an unregister callable. Tracked in BACKEND-GAPS.md.
```

`_hook_unregister` is always `None`. `_cleanup_hook()` always no-ops. On each reconnect, a new set of event hooks is registered but the old set is never removed. Dead hooks accumulate across reconnect cycles, all firing against a stale event queue.

**Fix:** `FoundationBackend._wire_event_queue` needs to return an unregister callable:
```python
def _wire_event_queue(self, session_id, queue) -> Callable:
    # ... register hooks ...
    def unregister():
        # ... remove hooks ...
    return unregister
```

---

## Hygiene / Dead Code

### P2 — `VOICE_TOOLS` in `__init__.py` is dead code with wrong format

**File:** `__init__.py:83`

`VOICE_TOOLS` uses the flat dict format without `type: 'function'` and is never passed to `VoiceConfig` — it's never sent to OpenAI. The browser-side registration (in `session.update` on `dc.onopen`) is what actually works.

The comment in `realtime.py` says tools are not supported at session creation time, but the GA `client_secrets` endpoint does accept tools. Either:
- Remove `VOICE_TOOLS` and the dead server-side path entirely, or
- Wire it into `VoiceConfig` with correct format if you want server-side registration at token creation

### P2 — `translator.py` is dead code

**File:** `translator.py`

`VoiceEventTranslator.translate()` maps data-channel event types to wire dicts but is never instantiated or called anywhere in the active code path. The browser handles its own event dispatch in `handleDataChannelEvent`. This class is orphaned — either delete it or document its intended purpose.

### P2 — `pause_replies`/`resume_replies` server path is dead code

**File:** `__init__.py:664-665`

Both tools are handled browser-locally before the server is ever contacted. The server's `/tools/execute` handler returns `{"result": "acknowledged"}` and does nothing. If server-side awareness of pause state is ever needed (e.g., to suppress Amplifier SSE events during pause), this path would need to be wired.

### P3 — `ConnectionHealthManager` duplicated

`ConnectionHealthManager` is defined in `static/connection-health.mjs` and identically inlined inside `static/index.html`. The `.mjs` file is never imported. Single source of truth doesn't exist.

---

## Summary Table

| Area | Current | Best Practice | Status |
|------|---------|---------------|--------|
| Transport | WebRTC (client) | WebRTC (client) | ✅ |
| Tool format | `{type:'function',...}` | `{type:'function',...}` | ✅ |
| Tool registration | `session.update` on connect | `session.update` on connect | ✅ |
| Result submission | `function_call_output` + `response.create` | Same | ✅ |
| Delegation pattern | Single `delegate` tool → Amplifier | Appropriate for this architecture | ✅ |
| Sub-agent spawning | `session.spawn` registered correctly | Correct | ✅ |
| Tool execution timing | `response.output_item.added` | `response.output_item.done` | ⚠️ P0 |
| Interruption handling | `responseInProgress` guard only | + `conversation.item.truncate` | ⚠️ P1 |
| VAD format (pause) | Beta flat format | GA nested format | ⚠️ P1 |
| Session expiry | TODO no-op | Proactive reconnect | ❌ P1 |
| Hook lifecycle | No unregistration | Unregister on disconnect | ❌ P2 |
| Server-side `VOICE_TOOLS` | Dead code, wrong format | Remove or wire | ❌ P2 |
| `translator.py` | Orphaned class | Remove or wire | ❌ P2 |
| `ConnectionHealthManager` | Duplicated | Single source | ❌ P3 |

---

## Recommended Fix Order

1. **`response.output_item.done` instead of `response.output_item.added`** for tool execution — this is the one change most likely to manifest as silent breakage under load
2. **Fix `pause_replies` VAD format** — GA nested path; verify with `session.updated` event in console
3. **Add `conversation.item.truncate` on interruption** — prevents gradual context corruption
4. **Wire proactive reconnect** — `ConnectionHealthManager` detection is already in place
5. **Remove dead code** — `VOICE_TOOLS`, `translator.py`, duplicate `ConnectionHealthManager`
6. **Fix hook unregistration** — requires `FoundationBackend._wire_event_queue` to return an unregister callable

---

## Amplifier Usage Evaluation

**Date:** 2026-02-27
**Focus:** Provider usage, direct LLM vendor SDK usage, Amplifier pattern compliance

### Direct LLM Vendor SDK Usage

**None found.**

Zero `import openai`, `import anthropic`, `import google.generativeai`, or any other LLM vendor SDK appear anywhere in the application source. `pyproject.toml` is clean — `amplifier-foundation` is the only LLM runtime dependency. Vendor SDKs live inside foundation's provider modules where they belong.

### Architecture Split

```
Voice Transport (OpenAI Realtime):
  Browser → GET /voice/session → realtime.py → httpx → api.openai.com/v1/realtime
  Browser ←→ OpenAI (WebRTC P2P audio — never through server)

Agent/Tool Backend (Amplifier):
  OpenAI tool call → POST /voice/tools/execute → FoundationBackend.send_message()
                  → session.execute() → amplifier_foundation (provider module)
```

The realtime voice transport uses raw `httpx` for WebRTC signaling. This is the accepted pattern for voice apps in the ecosystem — audio is an external transport concern, not an Amplifier concern. All reasoning, delegation, and tool work routes through `session.execute()`.

### What Is Working Correctly

**Tool calling and delegation.** When the realtime model invokes `delegate`, `cancel_current_task`, `pause_replies`, or `resume_replies`, those calls route to `FoundationBackend.send_message()` → `session.execute()`. Amplifier owns the agent loop; the realtime model owns the conversational/audio loop. The boundary is correct.

**Text/agent sessions are fully abstracted.** All LLM calls for the agent backend go through `amplifier_foundation.load_bundle()` → `prepared.create_session()` → `session.execute()`. No provider is called directly from application code.

**Hook system.** `ALL_EVENTS` is registered, `HookResult` is the return type for all hook implementations, and `EventStreamingHook` maps all 24+ canonical events to SSE wire events. The full observability pipeline is intact on the agent side.

**Provider catalog.** `features.py` declares providers using `foundation:providers/` includes and proper git source URIs. Credentials flow through `~/.amplifier/keys.env` → `startup.py:export_keys()`. Nothing hardcoded or committed.

**`amplifier_core` contracts.** `HookResult` and `ALL_EVENTS` are imported from `amplifier_core` — not shimmed or re-implemented.

### Minor Issues

**Voice API key sourcing is inconsistent.** Every other provider flows through `keys.env` → `export_keys()` → the provider catalog's `env_var` field. The voice route is the only exception:

```python
# server/apps/voice/__init__.py
openai_api_key=os.environ.get("OPENAI_API_KEY", "")   # direct env read
```

This works in practice because `export_keys()` runs at startup and populates `OPENAI_API_KEY`. But if a user's key is stored under a different name or the install wizard normalizes key names, voice silently gets an empty key with no error.

**`VoiceSettings.model` is freestanding.** The `features.py` openai provider entry has its own `default_model`. `VoiceSettings.model = "gpt-4o-realtime-preview"` is independent and won't follow if the catalog default is ever updated.

### Summary

| Area | Status | Notes |
|------|--------|-------|
| Direct vendor SDK imports | **Clean** | Zero in application code |
| Text/agent sessions | **Correct** | `session.execute()` throughout |
| Tool calling / delegation | **Correct** | Bridges properly into Amplifier |
| Hook system | **Correct** | `ALL_EVENTS`, `HookResult`, persistence |
| Provider catalog config | **Correct** | `foundation:providers/` includes, git URIs |
| Credential management | **Mostly correct** | Voice reads `OPENAI_API_KEY` directly; minor inconsistency |
| Realtime transport (httpx) | **Accepted** | Established pattern; audio is external to Amplifier |
| `VoiceSettings.model` | **Minor** | Not linked to provider catalog default |

Overall: the voice app follows the correct Amplifier architecture. The agent/tool boundary is properly drawn, all reasoning work goes through `session.execute()`, and the observability and hook infrastructure are intact on the side that matters.
