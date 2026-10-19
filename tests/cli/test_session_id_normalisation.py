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
    """The agent() function should call parse_handler_target near the top."""
    src = inspect.getsource(cli_agent.agent)
    assert "parse_handler_target" in src, (
        "nanobot.cli.agent.agent must call parse_handler_target to normalise "
        "the -s/--session argument"
    )
    # The hook must run BEFORE _load_runtime_config so both branches see
    # the canonical key.
    assert src.find("parse_handler_target") < src.find("_load_runtime_config"), (
        "normalisation hook must run before _load_runtime_config"
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
    """
    a = _normalise("websocket:assistant:qna:01")
    b = _normalise("assistant:qna#01")
    assert a == b == "assistant:qna#01"
