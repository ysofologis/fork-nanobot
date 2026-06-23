# Agent-Colab Feature Isolation Guide

## Goal

Agent-collaboration (agent-colab) features are isolated in `nanobot/ext/agent_collab/`
so that future merges from `orig/main` (upstream) cause minimal conflicts.

## Architecture

```
nanobot/ext/agent_collab/
├── __init__.py          — Package API (re-exports all public symbols)
├── router.py            — Agent-routing logic (|bot-name> parse, forward, reply)
├── runner_ext.py        — Runner extensions (goal-continue, budget finalization)
├── agent_binding.py     — Cron agent-binding enforcement
├── context_ext.py       — Context-building extensions (bot prompts, agent_id)
└── gateway_helpers.py   — Gateway signal/shutdown handlers
```

## How Isolation Works

Each file in the main codebase that was modified by agent-colab now either:

1. **Delegates** to `ext.agent_collab` via a thin import-and-call.
2. **Marks** the change with a `# agent-colab` comment.

### Delegation Pattern (preferred)

```python
# File: nanobot/agent/loop.py
from nanobot.ext.agent_collab.router import parse_agent_route
...
target_agent, clean = parse_agent_route(raw)
```

### Comment Marker Pattern

```python
# agent-colab: added agent_id parameter
def __init__(self, ..., agent_id: str | None = None):
    self.agent_id = agent_id  # agent-colab
```

## Where Agent-Colab Features Live

| Feature | Extracted To | Files with Delegation |
|---------|-------------|----------------------|
| Agent routing (`\|bot-name>` syntax) | `ext.agent_collab.router` | `nanobot/agent/loop.py` |
| Runner extensions (goal continue, budget) | `ext.agent_collab.runner_ext` | `nanobot/agent/runner.py` |
| Cron agent-binding enforcement | `ext.agent_collab.agent_binding` | `nanobot/cron/service.py` |
| Gateway signal/shutdown handlers | `ext.agent_collab.gateway_helpers` | `nanobot/cli/commands.py` |
| Agent ID in context builder | Inline `# agent-colab` markers | `nanobot/agent/context.py` |
| InboundMessage.sender field | Inline `# agent-colab` marker | `nanobot/bus/events.py` |

## Already Separate (New Files)

These were added by agent-colab and are entirely new — they live in their own
directories and cause **zero** merge conflicts with upstream:

- `nanobot/bus/` — Multi-backend bus (NATS, Redis, ZMQ, factory, config)
- `nanobot/gateway/` — Gateway service (runtime, service)
- `nanobot/sdk/` — Python SDK (clients, runtime, streaming, types)
- `nanobot/cli/gateway.py` — Gateway CLI command

## Merge Workflow

When merging upstream changes from `orig/main`:

1. **Pull upstream:**
   ```
   git checkout main
   git pull orig main
   ```

2. **Merge the `ext/` package first** (it's entirely ours — no conflicts):
   ```
   git checkout feat/agent-colab -- nanobot/ext/
   ```

3. **Resolve conflicts in delegated files:**
   Look at each conflicted file and check whether the conflict is in
   agent-colab code (marked `# agent-colab`) or upstream code.

4. **Update delegation calls:**
   If upstream changed the interface that agent-colab wraps, update the
   thin delegation call rather than re-inlining the agent-colab logic.

## Conflict Resolution Examples

### loop.py — Agent Routing

```
<<<<<<< HEAD (agent-colab)
target_agent, clean = parse_agent_route(raw)
=======
# upstream may have different message processing
>>>>>>> orig/main
```

**Resolution:** Keep the agent-colab line; add upstream's changes around it.
The agent-routing logic itself is safely in `ext.agent_collab.router`.

### runner.py — Goal Continue

```
<<<<<<< HEAD (agent-colab)
return build_goal_continue_message_from_spec(spec)
=======
# upstream's original implementation
>>>>>>> orig/main  
```

**Resolution:** Keep the agent-colab delegation call. The implementation
lives in `ext.agent_collab.runner_ext`.

### cron/service.py — Agent Binding

```
<<<<<<< HEAD (agent-colab)
return _ac_enforce_agent_binding(job)
=======
# upstream's cron handling
>>>>>>> orig/main
```

**Resolution:** Keep delegation. Binding logic is in `ext.agent_collab.agent_binding`.

## When to Add New Files to the Ext Package

When adding new agent-colab features:

1. **Put new logic in `nanobot/ext/agent_collab/`** (one file per concern).
2. **In existing upstream files**, add only a thin import-and-call.
3. **Mark all inline additions** with `# agent-colab` comments.

## Verifying the Isolation

After any merge:

```bash
# All files parse correctly
for f in nanobot/ext/agent_collab/*.py; do python3 -c "import ast; ast.parse(open('$f').read())" && echo "$f OK"; done

# Check that delegation imports resolve
python3 -c "from nanobot.ext.agent_collab import router; print('router', router.parse_agent_route('test'))"
```

## Summary of All Agent-Colab Changes

| File | Type of Change | Impact Level |
|------|---------------|-------------|
| `nanobot/ext/agent_collab/*.py` | **New** — all agent-colab logic | None on merge |
| `nanobot/bus/nats.py` | **New** — NATS backend | None on merge |
| `nanobot/bus/redis.py` | **New** — Redis backend | None on merge |
| `nanobot/bus/zmq.py` | **New** — ZMQ backend | None on merge |
| `nanobot/bus/config.py` | **New** — Bus config | None on merge |
| `nanobot/bus/factory.py` | **New** — Bus factory | None on merge |
| `nanobot/gateway/*.py` | **New** — Gateway service | None on merge |
| `nanobot/sdk/*.py` | **New** — Python SDK | None on merge |
| `nanobot/agent/loop.py` | Delegation + routing logic | LOW (thin calls) |
| `nanobot/agent/runner.py` | Delegation + ext methods | LOW (thin calls) |
| `nanobot/cron/service.py` | Delegation to agent_binding | LOW (thin calls) |
| `nanobot/cli/commands.py` | Delegation to gateway_helpers | LOW (import change) |
| `nanobot/agent/context.py` | Inline `# agent-colab` markers | LOW (comments only) |
| `nanobot/bus/events.py` | Inline `# agent-colab` marker | LOW (comment only) |
| `nanobot/bus/__init__.py` | Extended exports | LOW |
| `nanobot/bus/queue.py` | Agent-inbound features | MODERATE |
| `nanobot/nanobot.py` | SDK integration (full rewrite) | MODERATE |
| `nanobot/config/schema.py` | BusConfig, higher defaults | MODERATE |
| `nanobot/session/manager.py` | Session TTL + agent_id | LOW |
| `nanobot/security/workspace_policy.py` | Extra allowed roots | LOW |

Where impact is MODERATE, the code is self-contained (additive, not modifying
upstream logic) so conflicts are limited to method-signature clashes.
