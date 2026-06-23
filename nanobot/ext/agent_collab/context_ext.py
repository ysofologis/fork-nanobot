"""
Context-Building Extensions
===========================

Extracted from ``nanobot/agent/context.py``.  Provides the ``agent_id``
injection into runtime context metadata and bot-specific prompt file loading
that the agent-colab feature adds.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from nanobot.config.paths import get_workspace_path
from nanobot.utils.prompt_templates import render_template

logger = logging.getLogger(__name__)


def build_bot_specific_prompt(
    agent_id: str,
    workspace: str | Path | None = None,
) -> str | None:
    """Load a per-bot system prompt from the filesystem.

    Looks for ``bot_prompts/{agent_id}.md`` inside the bots directory in the
    workspace (or a fallback path).  Returns the rendered content or ``None``.
    """
    ws = get_workspace_path(workspace) if workspace else get_workspace_path()
    prompt_dir = Path(ws) / "bot_prompts"
    prompt_file = prompt_dir / f"{agent_id}.md"
    if not prompt_file.is_file():
        return None
    try:
        return render_template(str(prompt_file), strip=True)
    except Exception:
        logger.exception("Failed to load bot-specific prompt: %s", prompt_file)
        return None


def inject_agent_id_runtime_context(
    runtime_context: dict[str, Any] | None,
    agent_id: str | None,
) -> dict[str, Any] | None:
    """Inject the current agent identity into the runtime-context metadata dict.

    Returns the (possibly updated) *runtime_context* dict.
    """
    if agent_id:
        rc = runtime_context if runtime_context is not None else {}
        rc["agent_id"] = agent_id
        return rc
    return runtime_context
