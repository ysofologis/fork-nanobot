"""Tests for the /handler slash command and its grammar parser.

These tests run the pure parser (no AgentLoop) and a thin stub of
CommandContext for the command handler — the goal is to lock the contract
without coupling to upstream nanobot internals.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

import pytest

from nanobot.bus.events import InboundMessage, OutboundMessage
from nanobot.command.handler_grammar import (
    HandlerGrammarError,
    HandlerTarget,
    KNOWN_CHANNELS,
    parse_handler_target,
)
from nanobot.command.handler_cmds import cmd_handler
from nanobot.command.router import CommandContext


# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------


@dataclass
class _StubSession:
    key: str
    metadata: dict[str, Any] = field(default_factory=dict)
    messages: list[dict[str, Any]] = field(default_factory=list)


class _StubSessions:
    """Stub SessionManager: returns a session with the requested key."""

    def __init__(self) -> None:
        self._created: list[str] = []

    def get_or_create(self, key: str) -> _StubSession:
        self._created.append(key)
        return _StubSession(key=key)


class _StubLoop:
    """Minimal AgentLoop stub exposing only what cmd_handler needs."""

    def __init__(self) -> None:
        self.sessions = _StubSessions()


def _ctx(args: str, *, channel: str = "websocket", chat_id: str = "websocket:abc",
         current_key: str = "websocket:assistant:qna") -> CommandContext:
    msg = InboundMessage(
        channel=channel, sender_id="u", chat_id=chat_id,
        content=f"/handler {args}".rstrip(),
    )
    return CommandContext(
        msg=msg, session=None, key=current_key, raw=f"/handler {args}".rstrip(),
        args=args, runtime=None, is_user_turn=True, turn_scopes=[], loop=_StubLoop(),
    )


def _run(coro):
    """Run a coroutine to completion (asyncio.run wrapper for tests)."""
    return asyncio.get_event_loop().run_until_complete(coro) if False else asyncio.new_event_loop().run_until_complete(coro)


# ---------------------------------------------------------------------------
# Grammar tests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("raw,default,expected", [
    # Canonical forms
    ("assistant:qna#01", "cli", "assistant:qna#01"),
    ("assistant:qna#1", "cli", "assistant:qna#1"),
    ("assistant:qna", "cli", "assistant:qna"),
    # WebUI form: websocket: prefix + ':' pack separator
    ("websocket:assistant:qna:01", "cli", "assistant:qna#01"),
    ("websocket:assistant:qna:1", "cli", "assistant:qna#1"),
    ("websocket:cli:any_2026-05-24:04", "cli", "cli:any_2026-05-24#04"),
    # Bare form: uses the default channel
    ("qna#01", "assistant", "assistant:qna#01"),
    ("qna", "assistant", "assistant:qna"),
    # Names with underscores/dashes (common in real sessions)
    ("cli:any_2026-05-24__#04", "cli", "cli:any_2026-05-24__#04"),
    # :NN pack separator (alternative to #NN)
    ("assistant:qna:01", "cli", "assistant:qna#01"),
])
def test_grammar_accepts_known_shapes(raw, default, expected):
    t = parse_handler_target(raw, default_channel=default)
    assert t.session_key == expected
    assert isinstance(t, HandlerTarget)
    assert t.channel in KNOWN_CHANNELS


@pytest.mark.parametrize("raw,default", [
    # Bare "websocket" is a transport prefix, never a channel
    ("websocket", "cli"),
    ("WEBSOCKET", "cli"),  # case-insensitive
    # Empty / partial
    ("", "cli"),
    ("assistant:", "cli"),
    (":qna#01", "cli"),
    ("assistant:#01", "cli"),
    # Unknown channel
    ("foo:qna#01", "cli"),
    # Non-digit pack suffix is part of the name, not a pack
    ("assistant:qna#ab", "cli"),
    # Trailing separator without digits
    ("assistant:qna#", "cli"),
    ("assistant:qna:", "cli"),
    # websocket: prefix with nothing after
    ("websocket:", "cli"),
])
def test_grammar_rejects_malformed(raw, default):
    with pytest.raises(HandlerGrammarError):
        parse_handler_target(raw, default_channel=default)


def test_grammar_known_channels_excludes_websocket():
    """The transport prefix 'websocket' is intentionally in KNOWN_CHANNELS so
    that 'websocket:foo:bar' parses as channel='foo' (after prefix strip) but
    bare 'websocket' is rejected via the websocket-prefix check."""
    # 'websocket' as a channel name (with no transport prefix) must fail.
    with pytest.raises(HandlerGrammarError):
        parse_handler_target("websocket:foo:bar", default_channel="cli")
    # Bare 'websocket' must fail.
    with pytest.raises(HandlerGrammarError):
        parse_handler_target("websocket", default_channel="cli")


# ---------------------------------------------------------------------------
# Command handler tests
# ---------------------------------------------------------------------------


def test_cmd_handler_canonical():
    output = _run(cmd_handler(_ctx("assistant:qna#01")))
    assert isinstance(output, OutboundMessage)
    assert "assistant:qna#01" in output.content
    assert "🎯" in output.content
    assert output.metadata["command_handler_target"] == "assistant:qna#01"
    assert output.metadata["command_handler_same"] is False


def test_cmd_handler_webui_form_normalises_to_canonical():
    output = _run(cmd_handler(_ctx("websocket:assistant:qna:01")))
    assert output.metadata["command_handler_target"] == "assistant:qna#01"
    assert output.metadata["command_handler_parsed"]["pack"] == "01"


def test_cmd_handler_bare_form_uses_default_channel():
    output = _run(cmd_handler(_ctx("qna#01", current_key="assistant:foo")))
    assert output.metadata["command_handler_target"] == "assistant:qna#01"
    assert output.metadata["command_handler_parsed"]["channel"] == "assistant"


def test_cmd_handler_same_session_short_circuits():
    output = _run(cmd_handler(_ctx("qna#01", current_key="assistant:qna#01")))
    assert output.metadata["command_handler_same"] is True
    assert "Already on this session" in output.content


@pytest.mark.parametrize("channel,chat_id", [
    ("cli", "cli:direct"),  # classic prompt — must reject
    ("cli", "telegram:user"),  # any non-websocket chat from cli — reject
    ("feishu", "feishu:user"),  # any non-TUI channel — reject
    ("telegram", "telegram:user"),  # channel = telegram, not TUI
])
def test_cmd_handler_rejects_non_tui_contexts(channel, chat_id):
    output = _run(cmd_handler(_ctx("assistant:qna#01", channel=channel, chat_id=chat_id)))
    assert "only available in TUI sessions" in output.content
    assert output.metadata["command_error"] == "tui_only"


def test_cmd_handler_accepts_websocket_chat_id():
    output = _run(cmd_handler(_ctx("assistant:qna#01", channel="cli", chat_id="websocket:abc123")))
    assert output.metadata["command_handler_target"] == "assistant:qna#01"


def test_cmd_handler_missing_args():
    output = _run(cmd_handler(_ctx("")))
    assert output.metadata["command_error"] == "missing_args"
    assert "Usage" in output.content
    assert "/handler <session-id>" in output.content


def test_cmd_handler_grammar_error_propagates():
    output = _run(cmd_handler(_ctx("foo:qna#01")))
    assert output.metadata["command_error"] == "grammar"
    assert "unknown channel" in output.content
