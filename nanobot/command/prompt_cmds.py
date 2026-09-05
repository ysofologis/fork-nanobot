"""``/prompt`` and ``/prompt-list`` slash commands.

``/prompt <name> [extra...]`` launches a named prompt stored as
``{workspace}/prompts/<name>.md`` by injecting its contents (plus any
trailing user text) into the agent turn.

``/prompt-list`` lists all launchable prompts under ``{workspace}/prompts/``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from nanobot.bus.events import OutboundMessage
from nanobot.command.router import CommandContext
from nanobot.utils.workspace_prompts import (
    WORKSPACE_PROMPT_MAX_CHARS,
    load_workspace_prompt_override,
)

# Sub-directories that hold prompts for other purposes and must not be
# advertised as launchable by name.
_NON_LAUNCHABLE_SUBDIRS = {"system_prompts"}

# Names that upstream uses for workspace-local prompt overrides; they live
# next to launchable prompts but are not meant to be launched as agent turns.
_NON_LAUNCHABLE_PROMPTS = {"dream", "evaluator"}


def _workspace(ctx: CommandContext) -> Path | None:
    """Resolve the workspace root from the command context's loop."""
    workspace: Path | None = getattr(ctx.loop, "workspace", None)
    if workspace is None:
        context = getattr(ctx.loop, "context", None)
        workspace = getattr(context, "workspace", None) if context else None
    if workspace is None:
        return None
    return Path(workspace)


def _prompts_dir(workspace: Path) -> Path:
    """Return the workspace prompts directory."""
    return workspace / "prompts"


def _prompt_file(workspace: Path, name: str) -> Path:
    """Return the on-disk path for a named launchable prompt."""
    return _prompts_dir(workspace) / f"{name}.md"


def _safe_name(name: str) -> bool:
    """Reject names that escape the prompts directory or are invalid filenames."""
    return bool(name) and "/" not in name and "\\" not in name and name not in {".", ".."}


def _text_reply(
    ctx: CommandContext,
    content: str,
    *,
    render_as: str = "text",
    extra_metadata: dict[str, Any] | None = None,
) -> OutboundMessage:
    return OutboundMessage(
        channel=ctx.msg.channel,
        chat_id=ctx.msg.chat_id,
        content=content,
        metadata={
            **dict(ctx.msg.metadata or {}),
            "render_as": render_as,
            **(extra_metadata or {}),
        },
    )


async def cmd_prompt(ctx: CommandContext) -> OutboundMessage | None:
    """``/prompt <name> [extra...]`` — launch a saved prompt as an agent turn.

    Loads ``{workspace}/prompts/<name>.md`` and replaces the inbound message
    content with the prompt body plus any trailing text after the prompt name.
    Returns ``None`` so the normal agent turn proceeds with the loaded prompt.
    """
    workspace = _workspace(ctx)
    if workspace is None:
        return _text_reply(ctx, "Error: workspace not available.")

    args = ctx.args.strip()
    if not args:
        return _text_reply(
            ctx,
            "Usage: `/prompt <name> [extra text...]`\n"
            "Launches a saved prompt from `prompts/<name>.md` as an agent turn.\n"
            "Use `/prompt-list` to see the available prompts.",
        )

    name, sep, extra = args.partition(" ")
    name = name.strip()
    extra = extra.strip() if sep else ""

    if not _safe_name(name):
        return _text_reply(
            ctx,
            f"Invalid prompt name `{name}`. Use a simple file name without slashes.",
        )

    path = _prompt_file(workspace, name)
    prompt_text, original_chars = load_workspace_prompt_override(path)
    if prompt_text is None:
        return _text_reply(
            ctx,
            f"No prompt named `{name}` at `prompts/{name}.md`.\n"
            "Use `/prompt-list` to see the available prompts.",
        )

    if original_chars > WORKSPACE_PROMPT_MAX_CHARS:
        # load_workspace_prompt_override truncates; surface that in metadata so
        # callers can react if needed.  The turn still proceeds with the
        # truncated prompt.
        ctx.msg.metadata = {
            **dict(ctx.msg.metadata or {}),
            "prompt_launched": name,
            "prompt_truncated": True,
        }
    else:
        ctx.msg.metadata = {
            **dict(ctx.msg.metadata or {}),
            "prompt_launched": name,
        }

    if extra:
        ctx.msg.content = f"{prompt_text}\n\n{extra}"
    else:
        ctx.msg.content = prompt_text

    return None


async def cmd_prompt_list(ctx: CommandContext) -> OutboundMessage:
    """``/prompt-list`` — list all launchable prompts under ``prompts/``."""
    workspace = _workspace(ctx)
    if workspace is None:
        return _text_reply(ctx, "Error: workspace not available.")

    prompts_dir = _prompts_dir(workspace)
    if not prompts_dir.is_dir():
        rel = (
            prompts_dir.relative_to(workspace)
            if prompts_dir.is_relative_to(workspace)
            else prompts_dir
        )
        return _text_reply(
            ctx,
            f"No prompts directory at `{rel}`.\n"
            "Create `prompts/<name>.md` files to make them launchable via `/prompt <name>`.",
        )

    entries: list[dict[str, str | int]] = []
    for path in sorted(prompts_dir.iterdir()):
        if not path.is_file() or path.suffix != ".md":
            continue
        stem = path.stem
        if stem in _NON_LAUNCHABLE_PROMPTS or any(
            part in _NON_LAUNCHABLE_SUBDIRS for part in path.relative_to(prompts_dir).parts
        ):
            continue
        text, original_chars = load_workspace_prompt_override(path)
        if text is None:
            continue
        desc = text.strip().splitlines()[0] if text.strip() else ""
        entries.append({
            "name": stem,
            "chars": original_chars,
            "first_line": desc[:120],
        })

    if not entries:
        return _text_reply(
            ctx,
            f"No launchable prompts found under `{prompts_dir}`.\n"
            "Create `prompts/<name>.md` files to make them launchable via `/prompt <name>`.",
        )

    lines = [f"**Prompts** ({len(entries)})", ""]
    for entry in entries:
        name = entry["name"]
        chars = entry["chars"]
        first_line = entry["first_line"]
        preview = f" — _{first_line}_" if first_line else ""
        lines.append(f"- `{name}`  ({chars} chars){preview}")
    lines.append("")
    lines.append("Launch one with `/prompt <name> [extra text...]`.")
    return _text_reply(ctx, "\n".join(lines))
