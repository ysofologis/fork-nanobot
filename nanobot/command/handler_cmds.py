"""``/handler <session-id>`` slash command — switch the active session.

The handler accepts three equivalent input shapes (see §7.2 of
``docs/custom-features-ys-nanobot.md`` for the full grammar):

1. ``assistant:qna#01`` — canonical form, channel + name + ``#NN`` pack suffix.
2. ``websocket:assistant:qna:01`` — WebUI-side equivalent; the ``websocket:``
   prefix is stripped and the ``:NN`` pack separator is normalised to ``#NN``.
3. ``qna#01`` — bare form, resolved against the current TUI session's channel.

The command does not move conversation history between sessions; it
returns a confirmation message naming the resolved session. Actual
session-key wiring on the TUI side is the client's responsibility (same
pattern as :func:`cmd_pack`).

The command is TUI-only: invoking it from a non-interactive context
(single-message ``--message``, classic prompt, gateway, automations) raises
a clear error.
"""

from __future__ import annotations

from typing import Any

from nanobot.bus.events import OutboundMessage
from nanobot.command.handler_grammar import (
    HandlerGrammarError,
    parse_handler_target,
)
from nanobot.command.router import CommandContext


def _text_reply(
    ctx: CommandContext,
    content: str,
    *,
    extra_metadata: dict[str, Any] | None = None,
) -> OutboundMessage:
    return OutboundMessage(
        channel=ctx.msg.channel,
        chat_id=ctx.msg.chat_id,
        content=content,
        metadata={
            **dict(ctx.msg.metadata or {}),
            "render_as": "text",
            **(extra_metadata or {}),
        },
    )


def _is_tui_context(ctx: CommandContext) -> bool:
    """Return True when the command was issued from a native TUI session.

    Heuristic per §7.5 of the doc:

    - ``channel == "websocket"`` — WebUI surface uses this channel and is
      considered TUI-equivalent.
    - ``channel == "cli"`` and ``chat_id.starts_with("websocket:")`` — the
      TUI launcher's --classic pass routes through the cli channel with a
      websocket-prefixed chat id. Plain classic-prompt invocations
      (``chat_id == "cli:direct"``) are NOT accepted.
    """
    channel = ctx.msg.channel
    chat_id = ctx.msg.chat_id or ""
    if channel == "websocket":
        return True
    if channel == "cli" and chat_id.startswith("websocket:"):
        return True
    return False


async def cmd_handler(ctx: CommandContext) -> OutboundMessage:
    """``/handler <session-id>`` — switch the active session.

    Returns a confirmation message naming the resolved session key. The
    TUI client is responsible for binding the new key to subsequent
    inbound messages (mirroring ``/pack``).
    """
    if not _is_tui_context(ctx):
        return _text_reply(
            ctx,
            "**Error:** `/handler` is only available in TUI sessions.\n\n"
            "Use `nanobot agent --classic` or the native TUI launcher, then "
            "type `/handler <session-id>` to switch.",
            extra_metadata={"command_error": "tui_only"},
        )

    raw = (ctx.args or "").strip()
    if not raw:
        return _text_reply(
            ctx,
            "**Usage:** `/handler <session-id>`\n\n"
            "Examples:\n"
            "- `/handler assistant:qna#01`\n"
            "- `/handler websocket:assistant:qna:01` (WebUI form, prefix stripped)\n"
            "- `/handler qna#01` (uses the current session's channel)",
            extra_metadata={"command_error": "missing_args"},
        )

    # Determine the default channel from the current session key.
    current_channel = ""
    if ":" in ctx.key:
        current_channel = ctx.key.split(":", 1)[0].strip()

    try:
        target = parse_handler_target(raw, default_channel=current_channel)
    except HandlerGrammarError as exc:
        return _text_reply(
            ctx,
            f"**Error:** {exc}",
            extra_metadata={"command_error": "grammar"},
        )

    target_key = target.session_key

    # Verify the target session exists in the store. If it does not, offer
    # to create it via /pack or by simply continuing — for now we surface
    # the missing state but still allow the user to "create on next turn"
    # semantics by reporting the resolved key.
    target_session = None
    try:
        target_session = ctx.loop.sessions.get_or_create(target_key)
    except Exception as exc:
        return _text_reply(
            ctx,
            f"**Error:** could not load session `{target_key}`: {exc}",
            extra_metadata={"command_error": "load_failed"},
        )

    same = ctx.key == target_key
    return _text_reply(
        ctx,
        "\n".join([
            f"🎯 **Handler**",
            f"   Session key: `{target_key}`",
            f"   Channel: `{target.channel}`",
            f"   Name: `{target.name}{('#' + target.pack) if target.pack else ''}`",
            "",
            (
                f"_Already on this session — no switch needed._"
                if same
                else f"_Set your session key to `{target_key}` for subsequent turns._"
            ),
        ]),
        extra_metadata={
            "command_handler_target": target_key,
            "command_handler_same": same,
            "command_handler_parsed": {
                "channel": target.channel,
                "name": target.name,
                "pack": target.pack,
            },
        },
    )
