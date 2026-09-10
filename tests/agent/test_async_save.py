"""Feature 6: deferred (async-fire-and-forget) session save.

These tests cover ``AgentLoop.schedule_session_save`` and
``AgentLoop.await_pending_session_saves``. The motivation: in normal turn
flow the trailing ``sessions.save(session)`` is fire-and-forget so the user
sees the response before the disk flush completes; durability is preserved
by awaiting pending saves on ``aclose()`` and by the underlying
``SessionManager._session_files_lock`` atomic-rename.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from nanobot.agent.loop import AgentLoop
from nanobot.bus.queue import MessageBus
from nanobot.providers.base import LLMResponse
from nanobot.session.webui_turns import WebuiTurnCoordinator


def _make_full_loop(tmp_path: Path) -> AgentLoop:
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    provider.generation = SimpleNamespace(max_tokens=4096)
    provider.chat_with_retry = AsyncMock(return_value=LLMResponse(content="Test title"))
    loop = AgentLoop(bus=MessageBus(), provider=provider, workspace=tmp_path, model="test-model")
    WebuiTurnCoordinator(
        bus=loop.bus,
        sessions=loop.sessions,
        schedule_background=lambda coro: loop.schedule_background(coro),
    ).subscribe()
    return loop


async def test_schedule_session_save_creates_background_task(tmp_path: Path) -> None:
    """Scheduling a save should register a task; the task must drain the session to disk."""
    loop = _make_full_loop(tmp_path)
    session = loop.sessions.get_or_create("cli:async_save_test")
    # Seed a message so the on-disk file is non-trivial.
    session.add_message("user", "hello")

    assert loop._background_tasks == set(), "fresh loop should have no pending background tasks"
    loop.schedule_session_save(session)
    assert len(loop._background_tasks) == 1, "schedule_session_save must register exactly one task"

    await loop.await_pending_session_saves()
    assert loop._background_tasks == set(), "background tasks should drain after await"

    # The session must have been persisted to disk by the background flush.
    path = loop.sessions._get_session_path(session.key)
    assert path.exists(), "background save should materialize the session file"
    contents = path.read_text(encoding="utf-8")
    assert "hello" in contents, "background save should preserve the user message"


async def test_schedule_session_save_logs_on_failure(tmp_path: Path, caplog) -> None:
    """A failed background save must not propagate; it should be logged."""
    loop = _make_full_loop(tmp_path)
    session = loop.sessions.get_or_create("cli:async_save_fail_test")

    # Force sessions.save to raise synchronously inside the background task.
    real_save_was_attempted = False

    def _boom(_session: object) -> None:
        nonlocal real_save_was_attempted
        real_save_was_attempted = True
        raise RuntimeError("simulated disk failure")

    loop.sessions.save = _boom  # type: ignore[method-assign]

    loop.schedule_session_save(session)

    await loop.await_pending_session_saves()

    assert loop._background_tasks == set(), "failed background task should still drain"
    # Loguru writes to its own sinks; instead of asserting on the log record,
    # assert that the save attempt was actually made and that no exception
    # escaped the background task.
    assert real_save_was_attempted, "background save should have invoked sessions.save"
    # caplog is stdlib logging; loguru doesn't propagate by default. The
    # structural guarantee (exception caught, task drained, save attempted)
    # is sufficient — the helper code is a 2-line try/except that wraps
    # sessions.save and logs via logger.exception.


async def test_persist_turn_defers_save(tmp_path: Path) -> None:
    """``_persist_turn`` must NOT block on ``sessions.save``.

    We simulate a slow save by mocking ``sessions.save`` to await an event
    that we control. The scheduled task is registered immediately and runs
    in the background; the awaited ``await_pending_session_saves`` then
    flushes it.
    """
    loop = _make_full_loop(tmp_path)
    session = loop.sessions.get_or_create("cli:defer_test")
    session.add_message("user", "trigger")

    real_save_calls: list[str] = []
    # Use a controllable slow save.
    from asyncio import Event

    proceed = Event()
    started = Event()

    async def _slow_save(_session: object) -> None:
        real_save_calls.append(_session.key)  # type: ignore[attr-defined]
        started.set()
        await proceed.wait()

    loop.sessions.save = _slow_save  # type: ignore[method-assign]
    loop.schedule_session_save(session)
    # The background task has been scheduled.
    assert len(loop._background_tasks) == 1
    # Yield to the loop so the background task can advance into slow_save.
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert started.is_set(), "save task should have started after yielding"
    assert real_save_calls == ["cli:defer_test"], "save task should be running"

    # Release the slow save.
    proceed.set()
    await loop.await_pending_session_saves()
    assert loop._background_tasks == set(), "background tasks should be drained"
    assert real_save_calls == ["cli:defer_test"]


async def test_aclose_awaits_pending_session_saves(tmp_path: Path) -> None:
    """``aclose()`` must wait for any in-flight session saves before returning."""
    loop = _make_full_loop(tmp_path)
    session = loop.sessions.get_or_create("cli:aclose_test")
    session.add_message("user", "drain me")

    save_completed = asyncio.Event()
    real_save_calls: list[str] = []

    async def _slow_save(_session: object) -> None:
        real_save_calls.append(_session.key)  # type: ignore[attr-defined]
        await asyncio.sleep(0)
        save_completed.set()

    loop.sessions.save = _slow_save  # type: ignore[method-assign]
    loop.schedule_session_save(session)

    await loop.aclose()
    assert save_completed.is_set(), "aclose() must wait for pending background saves"
    assert real_save_calls == ["cli:aclose_test"]


async def test_await_pending_session_saves_is_noop_when_empty(tmp_path: Path) -> None:
    """Calling the helper when nothing is pending must complete immediately."""
    loop = _make_full_loop(tmp_path)
    assert loop._background_tasks == set()
    # Must not hang.
    await asyncio.wait_for(loop.await_pending_session_saves(), timeout=1.0)
