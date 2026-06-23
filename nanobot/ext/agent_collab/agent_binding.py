"""
Agent Binding Enforcement
=========================

Extracted from ``nanobot/cron/service.py``.  Prevents unbound agent-turn cron
jobs from executing when they lack session-delivery context.

When a user creates a cron job that triggers an agent turn (``kind="agent_turn"``),
the job needs a *bound session* — a chat session that the job was created from.
If the job is migrated or imported without that binding, it cannot be routed and
should be disabled to avoid silent failures.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

UNBOUND_AGENT_JOB_REASON = (
    "agent cron payload is missing bound session delivery context; "
    "recreate it from a chat session"
)


def is_unbound_agent_job(job: object) -> bool:
    """Check whether *job* is an agent-turn cron job without a bound session.

    Uses duck-typing so it works with any job object that has ``payload.kind``
    and a callable ``is_bound_cron_job()`` (injected via the cron module).
    """
    import importlib

    payload = getattr(job, "payload", None)
    if payload is None:
        return False
    kind = getattr(payload, "kind", None)
    if kind != "agent_turn":
        return False

    try:
        cron_mod = importlib.import_module("nanobot.cron.service")
        is_bound = getattr(cron_mod, "is_bound_cron_job", None)
        if is_bound is not None:
            return not is_bound(job)
    except ImportError:
        pass
    return True


def enforce_agent_binding(job: object) -> bool:
    """Disable *job* if it is an unbound agent-turn job.

    Returns ``True`` if the job was modified (disabled), ``False`` otherwise.
    """
    if not is_unbound_agent_job(job):
        return False

    # Already disabled and recorded?  Skip re-processing.
    job_enabled = getattr(job, "enabled", True)
    job_state = getattr(job, "state", None)

    if (
        not job_enabled
        and job_state is not None
        and getattr(job_state, "next_run_at_ms", 0) is None
        and getattr(job_state, "last_status", "") == "error"
        and getattr(job_state, "last_error", "")
    ):
        return False

    # -- disable the job ------------------------------------------------
    job.enabled = False
    if job_state is not None:
        job_state.next_run_at_ms = None
        job_state.last_status = "error"
        job_state.last_error = UNBOUND_AGENT_JOB_REASON
    from nanobot.cron.service import _now_ms

    job.updated_at_ms = max(getattr(job, "updated_at_ms", 0), _now_ms())
    logger.warning(
        "Cron: disabled unbound agent job '%s' (%s): %s",
        getattr(job, "name", "?"),
        getattr(job, "id", "?"),
        UNBOUND_AGENT_JOB_REASON,
    )
    return True


def enforce_store_agent_bindings(store: object) -> bool:
    """Enforce agent binding on every job in *store*.

    Returns ``True`` if at least one job was modified.
    """
    jobs = getattr(store, "jobs", [])
    changed = False
    for job in jobs:
        changed = enforce_agent_binding(job) or changed
    return changed
