"""
Agent Collaboration Extension
=============================

This package isolates all agent-collaboration features that are *added* on top of
the upstream nanobot codebase. Every file under ``nanobot/ext/agent_collab/``
contains pure agent-colab logic with zero modifications to upstream interfaces.

The goal is to minimise merge conflicts when rebasing against ``orig/main``:
instead of embedded changes across 15+ upstream files, the delta is concentrated
here and the modified upstream files contain only thin delegation calls.

Sub-modules
-----------
- ``router`` — Agent-to-agent message routing (|bot-name> syntax)
- ``runner_ext`` — Runner extensions (goal continue, budget-exhausted finalization)
- ``agent_binding`` — Cron agent binding enforcement for unbound sessions
- ``context_ext`` — Context-building extensions (bot-specific prompts, agent_id)
- ``gateway_helpers`` — Gateway signal handlers and shutdown helpers
"""

from nanobot.ext.agent_collab.router import (
    AGENT_ROUTING_RE,
    parse_agent_route,
    forward_to_target_agent,
    receive_from_agent,
    route_agent_response,
)

from nanobot.ext.agent_collab.runner_ext import (
    GoalContinueMessage,
    build_goal_continue_message_from_spec,
    has_injection_content,
    budget_exhausted_finalization_messages,
    max_iterations_fallback,
)

from nanobot.ext.agent_collab.agent_binding import (
    UNBOUND_AGENT_JOB_REASON,
    is_unbound_agent_job,
    enforce_agent_binding,
    enforce_store_agent_bindings,
)

from nanobot.ext.agent_collab.context_ext import (
    build_bot_specific_prompt,
    inject_agent_id_runtime_context,
)

from nanobot.ext.agent_collab.gateway_helpers import (
    signal_name,
    ensure_gateway_tty_signal_mode,
    install_gateway_shutdown_handlers,
)

__all__ = [
    "AGENT_ROUTING_RE",
    "UNBOUND_AGENT_JOB_REASON",
    "GoalContinueMessage",
    "budget_exhausted_finalization_messages",
    "build_bot_specific_prompt",
    "build_goal_continue_message_from_spec",
    "enforce_agent_binding",
    "enforce_store_agent_bindings",
    "ensure_gateway_tty_signal_mode",
    "forward_to_target_agent",
    "has_injection_content",
    "inject_agent_id_runtime_context",
    "install_gateway_shutdown_handlers",
    "is_unbound_agent_job",
    "max_iterations_fallback",
    "parse_agent_route",
    "receive_from_agent",
    "route_agent_response",
    "signal_name",
]
