# Delegation & Sub-Session Spawning: Cross-App Comparison

## Context

This analysis was produced to answer the question:

> "I want you to review how delegation, or spawning, is handled in amplifier-distro chat, slack, and voice apps and compare that to how that is done in amplifier-app-cli. Give me a breakdown of the different approaches, what is similar, what is different, what could be improved."

The codebase surveyed spans four repositories in this workspace:
- `amplifier-app-cli/` — the reference CLI application
- `amplifier-distro/` — the multi-app distribution server (Chat, Slack, Voice, Routines)
- `amplifier-voice/` — the standalone voice app (OpenAI Realtime + Amplifier Foundation)

---

## High-Level Architecture

All four surfaces use the same **tool-delegate module** as the LLM-visible interface, and all ultimately route through `PreparedBundle.spawn()` from amplifier-foundation — except the CLI, which has its own deeper implementation in `session_spawner.py`. The key split is **who registers `session.spawn`**, **how sophisticated that registration is**, and **what happens to delegation events after they fire**.

```
CLI path:
DelegateTool → session.spawn capability → spawn_sub_session() → manual AmplifierSession
                                                                  ↑ full merge_configs()
                                                                  ↑ filter_tools/hooks
                                                                  ↑ cancellation wiring
                                                                  ↑ SessionStore.save()
                                                                  ↑ sys.path sharing
                                                                  ↑ mention resolver

Distro/Voice path:
DelegateTool → session.spawn capability → spawn_fn() → PreparedBundle.spawn()
                                                         ↑ foundation handles internals
```

This means the CLI has significantly more control and sophistication, while the distro and voice bridges are thinner wrappers — simpler to write but with fewer knobs.

---

## What's the Same Everywhere

| Aspect | Detail |
|--------|--------|
| **LLM interface** | All use `tool-delegate` — the LLM calls `delegate(agent, instruction, ...)` |
| **Capability pattern** | All register `session.spawn` on `coordinator` via `register_capability()` |
| **Agent name resolution** | All follow: `agent_configs dict` → `bundle.agents` → special `"self"` → `ValueError` |
| **Child bundle construction** | All build a `Bundle(providers, tools, hooks, instruction)` object for the child |
| **Context injection** | All pass `parent_messages` to the child (context formatted as text prepended to instruction by `tool-delegate`) |
| **Result shape** | All return `{response: str, session_id: str}` from the spawn call |
| **Grandchild delegation** | All re-register `session.spawn` on child sessions, enabling arbitrary nesting |

---

## App-by-App Breakdown

### amplifier-app-cli

**Key file:** `amplifier_app_cli/session_spawner.py`

**Registration:** `session_runner.py:register_session_spawning()` — registered at root session creation, re-registered on every child.

**Unique capabilities:**
- Full `merge_configs()` with `apply_spawn_tool_policy()` — parent config is filtered before merging with agent overlay
- Explicit `_filter_tools()` / `_filter_hooks()` as separate functions
- Cancellation propagation: `parent_cancellation.register_child(child_cancellation)` — **the only surface that does this**
- `SessionStore.save()` — transcript + full metadata written to disk with `parent_id`, `trace_id`, `agent_overlay`, `bundle_context`
- Module source resolver inherited from parent (critical for bundle mode)
- `sys.path` sharing so bundle packages are accessible in child
- `AppMentionResolver` with bundle mappings inherited
- Provider preference resolution includes glob model matching against live provider

**Event visibility:** TUI renders sub-agent nesting inline in the terminal.

**Approval:** Interactive stdin prompt — blocks waiting for user input.

**Execution model:** Runs in the same async loop as the parent session. Awaits completion synchronously from the parent's perspective.

---

### amplifier-distro: Chat app

**Key files:** `server/spawn_registration.py`, `server/apps/chat/connection.py`, `server/apps/chat/translator.py`

**Registration:** `spawn_registration.py:register_spawning()` — called inside `FoundationBackend.create_session()` and `_reconnect()`.

**Key distinction:** The only surface with **full streaming visibility into delegation**. Passes an `event_queue` at session creation, which wires ALL kernel events plus the four delegate-specific events (`delegate:agent_spawned`, `delegate:agent_resumed`, `delegate:agent_completed`, `delegate:error`) into an `asyncio.Queue`.

**Event translation** (`apps/chat/translator.py`):
- `tool:pre` on delegate → `{type: "tool_call"}` + queues `tool_call_id` in `_pending_delegates`
- `delegate:agent_spawned` → `{type: "session_fork", parent_id, child_id, agent, parent_tool_call_id}` — the correlation that drives nesting cards in the UI
- `tool:post` → `{type: "tool_result"}` with `session_id` + `parent_id` lineage fields

**Execution model:** Fire-and-forget `execute()` — returns immediately, events stream async through the queue.

**Approval:** `ApprovalSystem` bridged over WebSocket with `asyncio.Event` blocking — interactive approval works.

**Session persistence:** `TranscriptSaveHook` + `MetadataSaveHook` on `tool:post` and `orchestrator:complete`. Server restart survival via `_reconnect()` — loads transcript from disk, handles orphaned tool calls, re-injects context.

---

### amplifier-distro: Slack app

**Key files:** `server/apps/slack/sessions.py`, `server/apps/slack/events.py`

**Registration:** Same `register_spawning()` as chat — `session.spawn` is registered. But that's where the similarity ends.

**Key distinction:** **No event queue wired.** `create_session()` is called without the `event_queue` parameter. Delegation events fire and go nowhere.

**Execution model:** Blocking `send_message()` via a per-session `asyncio.Queue` and `_session_worker` task. All sub-agent work runs to completion before the response is returned to Slack. Concurrent messages for the same session are serialized through this queue.

**User experience:** Sub-agent delegation is completely transparent. User sends a message, waits, gets a final response. No indication that any delegation occurred, who ran, or what they did.

**Approval:** Auto-approve (headless mode).

---

### amplifier-distro: Voice app

**Key files:** `server/apps/voice/__init__.py`, `server/apps/voice/connection.py`

**Registration:** `FoundationBackend.create_session()` calls `register_spawning()` normally. The `VoiceConnection.create()` additionally registers a `"spawn"` (not `"session.spawn"`) capability that routes child session creation through the shared backend with the correct event queue. This appears to be belt-and-suspenders or a legacy override.

**Execution model:** The voice LLM (OpenAI Realtime via WebRTC) calls `delegate` as a function call. The browser POSTs to `POST /tools/execute`. The server calls `backend.send_message()` — blocking, returns the result as a JSON HTTP response. Events from the Amplifier session stream via SSE to the browser in parallel.

**Approval:** Auto-approve.

---

### amplifier-voice (standalone)

**Key file:** `voice_server/amplifier_bridge.py`

**Architecture is fundamentally different** from the other three. The voice model is a pure orchestrator — it cannot see filesystem, bash, or web tools at all. `REALTIME_TOOLS = {"delegate"}` is enforced at `get_tools_for_openai()`.

**Registration:** `AmplifierBridge._register_spawn_capability()` — custom `spawn_capability` closure registered directly on the coordinator. Also registers `session.resume`.

**Unique aspects:**
- Bundle injection at runtime — Anthropic provider and tool-delegate are programmatically appended to the loaded bundle before `prepare()`
- `exclude_tools: ["delegate"]` on spawned agents — prevents recursive delegation chains
- `_active_child_sessions` dict tracks in-flight spawned agents for cancellation awareness
- **Explicit cancellation TODO** at `_spawn_with_cancellation` lines 603-614 — child sessions don't propagate parent cancellation, acknowledged as a known gap

**Context:** Three separate systems coexist:
1. OpenAI manages the voice model's 128K context natively
2. `parent_messages` passed to `PreparedBundle.spawn()` for sub-agent context
3. JSONL transcript store at `~/.amplifier-voice/sessions/` for resumption
4. Resume injects history via `conversation.item.create` over the WebRTC data channel — text-only, audio is lost at session boundaries

**Results:** Text/JSON string returned as `function_call_output` to the Realtime API. System prompt instructs the voice model to summarize verbally rather than read raw output.

**Approval:** `VoiceApprovalSystem` with `AUTO_APPROVE` policy. Dangerous operations are handled via conversational consent (voice model asks before delegating), not interactive gates.

---

## Side-by-Side Comparison

| Aspect | CLI | Chat | Slack | Distro Voice | Voice (standalone) |
|--------|-----|------|-------|-------------|-------------------|
| **Spawn implementation** | `session_spawner.py` (manual) | `PreparedBundle.spawn()` | `PreparedBundle.spawn()` | `PreparedBundle.spawn()` | `PreparedBundle.spawn()` |
| **Config merging** | Full `merge_configs()` | Bundle object construction | Bundle object construction | Bundle object construction | Bundle object construction |
| **Cancellation propagation** | Full (child registered to parent) | Unclear (foundation) | Unclear (foundation) | Unclear (foundation) | Explicit TODO gap |
| **Event visibility** | TUI inline nesting | Full streaming + `session_fork` | None | SSE parallel stream | SSE parallel stream |
| **Execution model** | Async await in-process | Fire-and-forget + stream | Blocking work queue | Blocking HTTP POST | Blocking HTTP POST |
| **Approval** | Interactive stdin | Interactive WebSocket | Auto-approve | Auto-approve | Auto-approve |
| **Session persistence** | `SessionStore` (full) | Hooks on `tool:post` | Hooks on `tool:post` | Hooks on `tool:post` | JSONL transcript store |
| **Reconnect/resume** | Session store lookup | Transcript replay + re-register | Same as chat | Same as chat | `conversation.item.create` injection |
| **Tool filtering** | Explicit `_filter_tools()` | `child_bundle.spawn` config | Same | Same | Same |
| **Provider prefs** | Full glob resolution | Via `PreparedBundle.spawn()` | Same | Same | Same |

---

## What Could Be Improved

### 1. Cancellation propagation gap in voice (both)

Both voice implementations don't properly cancel child sessions when the parent is cancelled. The CLI has the pattern: `parent_cancellation.register_child(child_cancellation)`. This needs to surface in `PreparedBundle.spawn()` so all thin-wrapper implementations get it for free, rather than requiring each host to wire it manually. The standalone voice bridge even has a `TODO` comment pointing exactly to where this belongs (`amplifier_bridge.py:_spawn_with_cancellation` lines 603-614).

### 2. Slack has zero delegation visibility

There's no indication to the user that sub-agent work is happening. Adding an event queue to Slack's `create_session()` call would enable progressive thread updates — posting "Delegating to explorer..." as a reaction or interim message while the sub-agent runs. The infrastructure is already there in `FoundationBackend._wire_event_queue()`, it's just not connected.

### 3. Capability name inconsistency: `"spawn"` vs `"session.spawn"`

`VoiceConnection.create()` in distro registers capability name `"spawn"` while everything else registers `"session.spawn"`. The tool-delegate module calls `session.spawn`. This looks like dead code or naming drift — worth confirming the distro voice `"spawn"` registration is actually used or removing it.

### 4. `parent_messages` is marked unused in the CLI

`spawn_sub_session()` accepts `parent_messages` but explicitly notes it's unused (line 473-476) — context goes via the instruction text instead. But distro and standalone voice pass it to `PreparedBundle.spawn()` which presumably does use it. This inconsistency between the two spawn paths should be reconciled with a decision on the canonical way to pass context to child sessions.

### 5. Duplicate voice implementations

Both `amplifier-distro/server/apps/voice/` and `amplifier-voice/` implement voice integration differently. The standalone voice is more sophisticated (full `AmplifierBridge` with cancellation tracking, custom protocols, `VoiceApprovalSystem`). The distro voice is a thinner integration on the shared backend. This divergence will be a maintenance burden — they should either be unified or their architectural boundaries clearly documented.

### 6. Bundle injection at runtime in voice (standalone)

`AmplifierBridge.initialize()` programmatically appends the Anthropic provider and tool-delegate to the loaded bundle before `prepare()`. This is fragile — the `if not has_delegate` check guards against tool-delegate duplication but the provider injection has no such guard. Defining the voice bundle declaratively (the `bundles/voice.yaml` file exists but isn't used) would be cleaner and testable.

### 7. No per-app agent isolation in distro

All three distro apps (chat, slack, voice) share the same `FoundationBackend`, the same bundle, and the same agent registry. There's no mechanism to configure different agents or tool access policies for Slack vs. Chat vs. Voice without changing the entire bundle. The CLI's `merge_configs()` and spawn tool policy give much finer control over what each agent can do — that pattern could be adapted for per-app profiles in distro.

### 8. Thin `PreparedBundle.spawn()` wrapper creates a capability surface mismatch

The CLI gets cancellation, `sys.path` sharing, module source resolver inheritance, and mention resolver inheritance. The distro/voice implementations using `PreparedBundle.spawn()` get none of those unless foundation bakes them in. This means improvements to the CLI's spawn sophistication don't automatically benefit the other surfaces. Either foundation's `spawn()` needs to absorb more of that logic, or the three thin spawn registrations need to be more explicit about what they're opting out of.

---

*Analysis generated 2026-02-27. Workspace: `/Users/robotdad/Source/Work/distro-voice`*
