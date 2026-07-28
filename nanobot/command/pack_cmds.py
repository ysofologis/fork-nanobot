"""``/pack``, ``/pack-list``, ``/pack-search`` slash commands.

Relies on :class:`nanobot.session.pack_manager.PackManager` for storage
and search.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from nanobot.command.router import CommandContext
from nanobot.session.pack import SessionPackKey, format_session_key, parse_session_key
from nanobot.session.pack_manager import PackManager

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _pack_manager(ctx: CommandContext) -> PackManager | None:
    """Resolve a :class:`PackManager` from the command context's loop."""
    workspace: Path | None = getattr(ctx.loop, "workspace", None)
    if workspace is None:
        context = getattr(ctx.loop, "context", None)
        workspace = getattr(context, "workspace", None) if context else None
    if workspace is None:
        return None
    return PackManager(workspace)


def _next_index(pm: PackManager, name: str) -> int:
    """Return the next available pack index for *name*."""
    meta = pm.get_pack(name)
    if meta is None:
        return 1
    indices: list[int] = meta.get("indices", [])
    if not indices:
        return 1
    return max(indices) + 1


def _format_pack_table(packs: list[dict[str, Any]]) -> str:
    """Build a compact table of packs."""
    if not packs:
        return "*No session packs found.*"
    lines = ["📦 **Session Packs**", ""]
    lines.append(f"{'Name':<30} │ {'Sessions':<9} │ {'Updated':<19} │ Status")
    lines.append(f"{'─'*30}─┼─{'─'*9}─┼─{'─'*19}─┼─{'─'*10}")
    for p in packs:
        name = p.get("session_name", "?")
        count = p.get("session_count", 0)
        updated = p.get("updated", "?")[:19]
        status = p.get("status", "active")
        lines.append(f"{name:<30} │ {count:<9} │ {updated:<19} │ {status}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# command handlers
# ---------------------------------------------------------------------------


async def cmd_pack(ctx: CommandContext) -> str:
    """``/pack <name>`` — Create or switch to a session pack.

    Returns a message with the new session key that the caller should
    use for subsequent requests.
    """
    pm = _pack_manager(ctx)
    if pm is None:
        return "Error: workspace not available."

    name = ctx.args.strip()
    if not name:
        return "Usage: `/pack <topic-name>`"

    # Determine next index
    idx = _next_index(pm, name)

    # Build the new session key from the current key's channel prefix
    current = parse_session_key(ctx.key)
    channel = current.channel or ""
    new_key = format_session_key(SessionPackKey(
        channel=channel,
        session_name=name,
        index=idx,
    ))

    # Ensure pack directory exists
    pm.resolve(new_key)

    return (
        f"📦 Switched to session pack **{name}**\n"
        f"   Session key: `{new_key}`\n"
        f"   Package index: #{idx:02d}\n\n"
        f"_Set your session key to `{new_key}` for subsequent turns._"
    )


async def cmd_pack_list(ctx: CommandContext) -> str:
    """``/pack-list`` — List all session packs."""
    pm = _pack_manager(ctx)
    if pm is None:
        return "Error: workspace not available."
    packs = pm.list_packs()
    return _format_pack_table(packs)


async def cmd_pack_search(ctx: CommandContext) -> str:
    """``/pack-search <query>`` — Search across session packs."""
    pm = _pack_manager(ctx)
    if pm is None:
        return "Error: workspace not available."

    query = ctx.args.strip()
    if not query:
        return "Usage: `/pack-search <query>`"

    results = pm.search(query)
    if not results:
        return f"No results for {query!r}."

    lines = [f"🔍 **Search results for** `{query}`", ""]
    for r in results:
        lines.append(
            f"  **{r['session_name']}**  (score: {r['score']:.2f}, "
            f"match: {r['match_type']})"
        )
        lines.append(f"  _{r['snippet']}_")
        lines.append("")
    return "\n".join(lines)
