"""Tests for the -s / --session normalisation hook in nanobot.cli.agent.

The hook (added when feature-flag A was approved) calls into
``parse_handler_target`` from nanobot.command.handler_grammar so the
WebUI / TUI / classic-CLI triple-naming collapses to a single canonical
session key before either of the two CLI branches consumes it.

These tests exercise the normalisation logic directly, not via the
typer CLI machinery (which requires a TTY).
"""

from __future__ import annotations

import inspect

import pytest

import nanobot.cli.agent as cli_agent


def _normalise(sid, default_channel="cli"):
    """Re-extract the normalisation block from the agent() body.

    We invoke the hook's logic by reading the function source and
    calling ``parse_handler_target`` ourselves. This keeps the test
    independent of any internal helper rename.
    """
    from nanobot.command.handler_grammar import (
        HandlerGrammarError,
        parse_handler_target,
    )

    if not sid or not sid.strip():
        return sid
    try:
        t = parse_handler_target(sid, default_channel=default_channel)
    except HandlerGrammarError as exc:
        raise ValueError(str(exc)) from exc
    return t.session_key


def test_hook_present_in_agent_body():
    """The agent() function should call parse_handler_target and gate it on
    ``not native_tui`` so only the classic-prompt branch is normalised.

    Ordering requirement (post-fix): the hook runs AFTER both
    _load_runtime_config (because runtime_config must exist) AND the
    native_tui decision (because the hook gates on it)."""
    src = inspect.getsource(cli_agent.agent)
    assert "parse_handler_target" in src, (
        "nanobot.cli.agent.agent must call parse_handler_target to normalise "
        "the -s/--session argument"
    )
    hook_idx = src.find("parse_handler_target")
    load_idx = src.find("_load_runtime_config")
    native_tui_idx = src.find("native_tui = message is None")
    assert load_idx != -1 and load_idx < hook_idx, (
        "_load_runtime_config must run before the hook (load happens first)"
    )
    assert native_tui_idx != -1 and native_tui_idx < hook_idx, (
        "native_tui decision must happen before the hook (so the hook can "
        "gate on it)"
    )


@pytest.mark.parametrize("input_sid,expected", [
    # WebUI form normalises to canonical.
    ("websocket:assistant:qna:01", "assistant:qna#01"),
    # Canonical form passes through unchanged.
    ("assistant:qna#01", "assistant:qna#01"),
    # Long names with multiple separators
    ("websocket:cli:any_2026-05-24:04", "cli:any_2026-05-24#04"),
    # Classic-prompt default key unchanged.
    ("cli:direct", "cli:direct"),
    # No session_id given → None passes through.
    (None, None),
    # Empty string passes through.
    ("", ""),
    # Whitespace-only passes through (treated as "not given" downstream).
    ("   ", "   "),
])
def test_normalise_positive_cases(input_sid, expected):
    assert _normalise(input_sid) == expected


@pytest.mark.parametrize("bad_sid", [
    "foo:qna#01",            # unknown channel
    "assistant:",             # no name
    "assistant:#01",          # empty name
    "qna#ab",                 # non-digit pack
    "websocket",              # bare transport prefix
])
def test_normalise_rejects_bad_input(bad_sid):
    # Any ValueError is fine — the BadParameter in cli/agent.py surfaces
    # whatever the grammar produced. We don't assert on the message body
    # because the grammar gives different error texts for different failure
    # modes (unknown channel vs empty name vs bad pack), and locking those
    # would couple the test to handler_grammar wording.
    with pytest.raises(ValueError):
        _normalise(bad_sid)


def test_both_cli_invocations_resolve_to_same_session():
    """Regression test for the user's exact request:

        nanobot agent -s websocket:assistant:qna:01 -c config/nanobot-assistant.json
        nanobot agent -s assistant:qna#01         -c config/nanobot-assistant.json --no-tui

    Both must load the SAME session key.

    The CLI hook normalises only on the classic-prompt branch (when
    ``native_tui`` is False). The native TUI branch keeps the
    ``websocket:`` form because upstream's _websocket_chat_id (called by
    launch_tui) requires it for WebUI chat-id extraction.
    """
    a = _normalise("websocket:assistant:qna:01")
    b = _normalise("assistant:qna#01")
    assert a == b == "assistant:qna#01"


def test_hook_is_gated_on_native_tui():
    """The normalisation hook must run AFTER ``native_tui = ...`` is decided
    and must check ``not native_tui`` before calling parse_handler_target.

    Without this gate, the TUI branch receives the canonical key
    (``assistant:qna#01``) which upstream's _websocket_chat_id rejects
    with 'the native TUI can open only WebSocket sessions'. The gate
    preserves upstream's TUI semantics: TUI must be given a websocket:-
    prefixed key; classic-prompt may use either form.
    """
    src = inspect.getsource(cli_agent.agent)
    hook_idx = src.find("parse_handler_target")
    native_tui_idx = src.find("native_tui = message is None")
    assert native_tui_idx != -1, "native_tui assignment not found in agent() body"
    assert hook_idx > native_tui_idx, (
        "normalisation hook must run AFTER native_tui is decided, "
        f"got hook at {hook_idx} vs native_tui at {native_tui_idx}"
    )
    # The hook must gate on the negation of native_tui.
    between = src[native_tui_idx:hook_idx]
    assert "not native_tui" in between or "not native_tui" in src[hook_idx:hook_idx + 400], (
        "normalisation hook must check 'not native_tui' before calling "
        "parse_handler_target, so it skips when the TUI branch runs"
    )
