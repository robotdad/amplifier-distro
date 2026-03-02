"""Tests for spawn_registration.register_spawning()."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from amplifier_distro.server.spawn_registration import register_spawning

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_session(session_id: str = "parent-001") -> MagicMock:
    session = MagicMock()
    session.session_id = session_id
    session.config = {"agents": {}}
    coordinator = MagicMock()
    coordinator.register_capability = MagicMock()
    session.coordinator = coordinator
    return session


def _make_prepared(bundle_agents: dict | None = None) -> MagicMock:
    prepared = MagicMock()
    bundle = MagicMock()
    bundle.agents = bundle_agents or {}
    prepared.bundle = bundle
    prepared.spawn = AsyncMock(
        return_value={"response": "ok", "session_id": "child-001"}
    )
    return prepared


def _get_registered_spawn_fn(session: MagicMock):
    """Pull the registered spawn coroutine out of the mock."""
    session.coordinator.register_capability.assert_called_once()
    name, fn = session.coordinator.register_capability.call_args[0]
    assert name == "session.spawn"
    return fn


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_register_spawning_registers_capability():
    """register_spawning sets session.spawn on the coordinator."""
    session = _make_session()
    prepared = _make_prepared()
    register_spawning(session, prepared, "parent-001")

    session.coordinator.register_capability.assert_called_once()
    name = session.coordinator.register_capability.call_args[0][0]
    assert name == "session.spawn"


def test_registered_fn_is_coroutine():
    """The registered capability must be an async function."""
    session = _make_session()
    register_spawning(session, _make_prepared(), "parent-001")
    fn = _get_registered_spawn_fn(session)
    assert asyncio.iscoroutinefunction(fn)


@pytest.mark.asyncio
async def test_spawn_named_agent_from_agent_configs():
    """Named agent resolved from agent_configs calls prepared.spawn."""
    session = _make_session()
    prepared = _make_prepared()
    register_spawning(session, prepared, "parent-001")
    fn = _get_registered_spawn_fn(session)

    result = await fn(
        agent_name="my-agent",
        instruction="do work",
        parent_session=session,
        agent_configs={"my-agent": {"instruction": "You are helpful."}},
        sub_session_id="child-001",
    )

    prepared.spawn.assert_called_once()
    kw = prepared.spawn.call_args[1]
    assert kw["instruction"] == "do work"
    assert kw["session_id"] == "child-001"
    assert result == {"response": "ok", "session_id": "child-001"}


@pytest.mark.asyncio
async def test_spawn_named_agent_from_bundle_agents():
    """Falls back to prepared.bundle.agents when not in agent_configs."""
    session = _make_session()
    prepared = _make_prepared(
        bundle_agents={"bundle-agent": {"instruction": "Bundle."}}
    )
    register_spawning(session, prepared, "parent-001")
    fn = _get_registered_spawn_fn(session)

    await fn(
        agent_name="bundle-agent",
        instruction="go",
        parent_session=session,
        agent_configs={},
        sub_session_id="child-002",
    )
    prepared.spawn.assert_called_once()


@pytest.mark.asyncio
async def test_spawn_self_uses_empty_config():
    """'self' is always allowed and spawns with empty config."""
    session = _make_session()
    prepared = _make_prepared()
    register_spawning(session, prepared, "parent-001")
    fn = _get_registered_spawn_fn(session)

    await fn(
        agent_name="self",
        instruction="recurse",
        parent_session=session,
        agent_configs={},
        sub_session_id="child-003",
    )
    prepared.spawn.assert_called_once()


@pytest.mark.asyncio
async def test_spawn_unknown_agent_raises_value_error():
    """Unknown agent name raises ValueError with message containing 'not found'."""
    session = _make_session()
    prepared = _make_prepared()
    register_spawning(session, prepared, "parent-001")
    fn = _get_registered_spawn_fn(session)

    with pytest.raises(ValueError, match="not found"):
        await fn(
            agent_name="ghost",
            instruction="fail",
            parent_session=session,
            agent_configs={},
            sub_session_id="child-bad",
        )


@pytest.mark.asyncio
async def test_provider_preferences_forwarded():
    """provider_preferences is passed through to prepared.spawn."""
    session = _make_session()
    prepared = _make_prepared()
    register_spawning(session, prepared, "parent-001")
    fn = _get_registered_spawn_fn(session)

    prefs = [{"provider": "anthropic", "model": "claude-*"}]
    await fn(
        agent_name="my-agent",
        instruction="go",
        parent_session=session,
        agent_configs={"my-agent": {}},
        sub_session_id="child-004",
        provider_preferences=prefs,
    )
    kw = prepared.spawn.call_args[1]
    assert kw["provider_preferences"] == prefs


@pytest.mark.asyncio
async def test_extra_kwargs_ignored():
    """Unknown kwargs from future tool-delegate versions do not crash spawn_fn."""
    session = _make_session()
    prepared = _make_prepared()
    register_spawning(session, prepared, "parent-001")
    fn = _get_registered_spawn_fn(session)

    # Should not raise
    await fn(
        agent_name="my-agent",
        instruction="go",
        parent_session=session,
        agent_configs={"my-agent": {}},
        sub_session_id="child-005",
        future_kwarg_from_2027="ignored",
    )
    prepared.spawn.assert_called_once()


@pytest.mark.asyncio
async def test_self_delegation_depth_forwarded():
    """self_delegation_depth is passed through to prepared.spawn."""
    session = _make_session()
    prepared = _make_prepared()
    register_spawning(session, prepared, "parent-001")
    fn = _get_registered_spawn_fn(session)

    await fn(
        agent_name="my-agent",
        instruction="go",
        parent_session=session,
        agent_configs={"my-agent": {}},
        sub_session_id="child-006",
        self_delegation_depth=3,
    )
    assert prepared.spawn.call_args[1]["self_delegation_depth"] == 3


@pytest.mark.asyncio
async def test_event_forwarder_called_with_delegating_agent():
    """When event_forwarder is provided, forwarding hook tags events with delegating_agent.

    Verifies:
    - A hook named 'delegation-event-forwarder' is appended to the child bundle
    - When that hook fires with a tool:pre event, event_forwarder is called
    - The forwarded dict contains delegating_agent == the agent name
    - The forwarded dict contains type == 'tool_call'
    """
    session = _make_session()
    prepared = _make_prepared(bundle_agents={"my-agent": {"instruction": "Be helpful."}})

    forwarded: list[dict] = []

    def event_forwarder(msg: dict) -> None:
        forwarded.append(msg)

    register_spawning(session, prepared, "parent-001", event_forwarder=event_forwarder)
    fn = _get_registered_spawn_fn(session)

    # Override spawn to capture the child_bundle argument so we can inspect its hooks
    captured_bundles: list = []

    async def capturing_spawn(child_bundle, **kwargs):
        captured_bundles.append(child_bundle)
        return {"response": "ok", "session_id": "child-001"}

    prepared.spawn = capturing_spawn

    await fn(
        agent_name="my-agent",
        instruction="do work",
        parent_session=session,
        agent_configs={"my-agent": {"instruction": "Be helpful."}},
        sub_session_id="child-001",
    )

    assert len(captured_bundles) == 1, "prepared.spawn() was not called"
    bundle = captured_bundles[0]

    # The forwarding hook must be present in the child bundle's hooks list
    forwarding_hook = next(
        (h for h in bundle.hooks if getattr(h, "name", "") == "delegation-event-forwarder"),
        None,
    )
    assert forwarding_hook is not None, (
        "No hook named 'delegation-event-forwarder' found in child bundle hooks. "
        f"Hooks present: {[getattr(h, 'name', repr(h)) for h in bundle.hooks]}"
    )

    # Fire a synthetic tool:pre event through the hook
    await forwarding_hook(
        "tool:pre",
        {"tool_name": "bash", "tool_call_id": "tc-001", "arguments": {"cmd": "ls"}},
    )

    assert len(forwarded) == 1, (
        f"event_forwarder was called {len(forwarded)} times, expected 1"
    )
    msg = forwarded[0]
    assert msg.get("delegating_agent") == "my-agent", (
        f"Expected delegating_agent='my-agent', got {msg.get('delegating_agent')!r}"
    )
    assert msg.get("type") == "tool_call", (
        f"Expected type='tool_call', got {msg.get('type')!r}"
    )
    assert msg.get("tool_name") == "bash"
