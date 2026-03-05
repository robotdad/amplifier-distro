# amplifier-distro Documentation Index

Quick-reference index for all documentation in this repository.

---

## Root

| Document | Description |
|----------|-------------|
| [README.md](../README.md) | Project overview, install instructions, experience apps, provider configuration |
| [SECURITY.md](../SECURITY.md) | Microsoft security vulnerability reporting process |
| [AMPLIFIER_HOME_CONTRACT.md](../AMPLIFIER_HOME_CONTRACT.md) | Canonical spec for the `~/.amplifier/` filesystem layout and path derivation rules |

---

## Architecture & Reviews

| Document | Description |
|----------|-------------|
| [voice-architecture.md](voice-architecture.md) | Voice app design reference -- WebRTC-first constraint, dual-session identity model |
| [voice-realtime-review.md](voice-realtime-review.md) | Technical review of Voice app OpenAI Realtime API integration |
| [DELEGATION-COMPARISON.md](DELEGATION-COMPARISON.md) | Cross-app analysis of delegation/sub-session spawning (CLI, Chat, Slack, Voice) |

---

## Design Documents

Architectural specs describing the *what* and *why* of a feature.

| Date | Document | Feature |
|------|----------|---------|
| 2026-07-08 | [session-name-editing-design](plans/2026-07-08-session-name-editing-design.md) | Inline session renaming in chat sidebar |
| 2026-03-10 | [session-history-perf-design](plans/2026-03-10-session-history-perf-design.md) | Performance fix for session history loading |
| 2026-03-03 | [secure-remote-access-design](plans/2026-03-03-secure-remote-access-design.md) | Four-layer secure remote access for voice app |
| 2026-03-03 | [brand-alignment-theme-design](plans/2026-03-03-brand-alignment-theme-design.md) | Theme redesign aligned with withamplifier brand |
| 2026-03-01 | [chat-to-voice-design](plans/2026-03-01-chat-to-voice-design.md) | Voice app resuming any Amplifier session |
| 2026-02-28 | [cross-device-websocket-design](plans/2026-02-28-cross-device-websocket-design.md) | WebSocket disconnection fix for LAN devices |
| 2026-02-27 | [delegation-feedback-design](plans/2026-02-27-delegation-feedback-design.md) | Visual/vocal delegation feedback in voice app |
| 2026-02-27 | [service-commands-fix-design](plans/2026-02-27-service-commands-amp-distro-fix-design.md) | Fix `amp-distro service` command routing |
| 2026-02-27 | [handoff-injection-design](plans/2026-02-27-handoff-injection-design.md) | Auto-inject recent project handoff into sessions |
| 2026-02-27 | [distro-remaining-issues](plans/2026-02-27-distro-remaining-issues.md) | Umbrella design for 6 outstanding issues |

---

## Implementation Plans

Step-by-step TDD task lists describing *how* to build a feature.

| Date | Document | Feature |
|------|----------|---------|
| 2026-07-08 | [session-name-editing-impl](plans/2026-07-08-session-name-editing-implementation.md) | Session renaming -- backend protocol, WebSocket broadcast, frontend edit mode |
| 2026-03-03 | [secure-remote-access-impl](plans/2026-03-03-secure-remote-access-implementation.md) | TLS, PAM auth, CSRF, browser secure-context guard |
| 2026-03-03 | [web-chat-responsiveness](plans/2026-03-03-web-chat-responsiveness-improvements.md) | Client-side thinking placeholder, orchestrator event surfacing |
| 2026-03-01 | [chat-to-voice-plan v2](plans/2026-03-01-chat-to-voice-plan.md) | Chat-to-voice handoff (revised 9-task plan) |
| 2026-02-27 | [chat-to-voice v1](plans/2026-02-27-chat-to-voice.md) | Chat-to-voice handoff (original 8-task plan) |
| 2026-02-27 | [fix-approval-display](plans/2026-02-27-fix-approval-display-plan.md) | `SessionSurface` abstraction replacing `event_queue` |
| 2026-02-27 | [fix-bundle-prewarm](plans/2026-02-27-fix-bundle-prewarm-plan.md) | Bundle pre-warming at server startup |
| 2026-02-27 | [service-commands-fix-impl](plans/2026-02-27-service-commands-amp-distro-fix-implementation.md) | Service command routing through `amp-distro` binary |
| 2026-02-27 | [fix-slack-aiohttp](plans/2026-02-27-fix-slack-aiohttp-plan.md) | Slack aiohttp session lifecycle cleanup |
| 2026-02-27 | [fix-overlay-hooks](plans/2026-02-27-fix-overlay-hooks-plan.md) | Move session-naming hook into default bundle |
| 2026-02-27 | [fix-await-cancel](plans/2026-02-27-fix-await-cancel-plan.md) | Async correctness: missing await + CWD guard |
| 2026-02-26 | [session-spawning](plans/2026-02-26-session-spawning.md) | Register `session.spawn` capability in `FoundationBackend` |

---

## Testing

| Document | Description |
|----------|-------------|
| [E2E Test Recipes](../.amplifier/recipes/README.md) | Staged E2E test system -- 27 scenarios across chat, cross-surface, and Slack |

---

## Navigating This Repository

- **Design docs** describe the approach for a feature and are useful for understanding intent and trade-offs.
- **Implementation plans** are paired with their design docs and contain TDD task lists with exact file paths -- designed for agent-driven execution.
- The **chat-to-voice** feature has two generations of plans (Feb 27 and Mar 1), reflecting design evolution.
- Several small "summary stub" files in `plans/` act as commit receipts pointing to the full documents and are not listed here.
