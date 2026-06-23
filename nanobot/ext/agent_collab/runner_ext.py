"""
Runner Extensions
=================

Extracted from ``nanobot/agent/runner.py``.  Contains the enhanced goal-continue
logic, budget-exhausted finalization, and injection-content validation that the
agent-colab feature adds on top of the upstream runner.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from nanobot.utils.prompt_templates import render_template

logger = logging.getLogger(__name__)

# A custom goal-continue message can be a static string or a
# zero-argument callable that returns a string (or None to fall back
# to the default).
GoalContinueMessage = str | Callable[[], str | None]


def build_goal_continue_message_from_spec(
    spec: object,
) -> dict[str, str]:
    """Build a goal-continue injection dict from ``AgentRunSpec``.

    If ``spec.goal_continue_message`` is a callable it is invoked; the result
    is forwarded to ``build_goal_continue_message`` (upstream utility).
    """
    from nanobot.agent.runner import build_goal_continue_message

    custom = getattr(spec, "goal_continue_message", None)
    if custom is not None and callable(custom):
        try:
            custom = custom()
        except Exception:
            logger.exception("goal_continue_message callback failed")
            custom = None
    return build_goal_continue_message(custom)


def has_injection_content(content: Any) -> bool:
    """Validate that an injection item actually carries meaningful content."""
    if content is None:
        return False
    if isinstance(content, str):
        return bool(content.strip())
    if isinstance(content, list):
        return bool(content)
    return True


def budget_exhausted_finalization_messages(
    messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Build a copy of *messages* with the budget-exhausted finalization prompt appended."""
    from nanobot.agent.runner import build_budget_exhausted_finalization_message

    retry = list(messages)
    retry.append(build_budget_exhausted_finalization_message())
    return retry


def max_iterations_fallback(spec: object) -> str:
    """Return the fallback text when max iterations is reached."""
    spec_max = getattr(spec, "max_iterations_message", None)
    spec_max_iter = getattr(spec, "max_iterations", 0)
    if spec_max:
        return spec_max.format(max_iterations=spec_max_iter)
    return render_template(
        "agent/max_iterations_message.md",
        strip=True,
        max_iterations=spec_max_iter,
    )
