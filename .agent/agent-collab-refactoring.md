# Agent Collaboration — Mainline Refactoring (Why `feat/agent-colab` Cannot Merge Into `main`)

> **Status**: Reference document for the `feat/agent-colab` branch
> **Scope**: Documents the bus refactoring on `main` that blocks merging agent-collab, and the merge strategy used by `merge/agent-colab` to keep the feature alive

---

## Context

Between implementation completion (~2026-06-20) and the current date, the `main` branch of `nanobot-app` underwent a series of refactorings that **removed the multi-backend pub/sub substrate** that agent-collab depends on. A parallel branch architecture was established to preserve agent-collab while staying in sync with mainline improvements.

## The Core Conflict

| Branch | Bus State |
|--------|-----------|
| `main` (HEAD) | Simplified bus — **in-process only**, NATS/Redis/ZMQ backends removed, pub/sub (`subscribe_outbound`/`dispatch_outbound`) deleted |
| `feat/agent-colab` | Full bus — NATS, Redis, ZMQ backends preserved + agent collaboration additions (cross-agent messaging, target routing, reply/forward) |
| `merge/agent-colab` (protected) | Full bus preserved, non-bus bugfixes from `main` merged in |

Agent-collab requires the bus to support:

- Agent identity (`agent_id`)
- Cross-instance routing via NATS/Redis/ZMQ subjects
- Agent-to-agent message publishing (`publish_agent_message`, `consume_agent_message`)
- Subscription-based dispatch to specific agent queues

Mainline refactoring removed exactly this infrastructure, rendering the `feat/agent-colab` branch unmergeable into `main` without a major reconciliation effort.

## Refactoring Commits on `main` That Block the Merge

| Commit | Scope | What Changed | Collab Impact |
|--------|-------|--------------|---------------|
| `0001f286` | `bus/queue.py` | Removed `subscribe_outbound()`, `dispatch_outbound()`, `stop()` — the pub/sub subscriber dispatch loop and its associated dict of channel → callbacks, plus unused imports (`Callable`, `Awaitable`, `logger`) | Agent-collab depends on subscription-based routing for agent-to-agent dispatch; this removed the pattern entirely |
| `2fd31463` | `agent/loop.py`, `bus/runtime_events.py` | Extracted runtime event publishing from the agent loop into a standalone `runtime_events.py` module; rewrote 144→36 lines in `loop.py` | Restructured the loop event pipeline, diverging from the loop code where agent-collab injected its route/reply hooks |
| `6267c607` | Various | Subscribed file-edit progress to typed runtime events via channel capability | Continued decoupling of event routing from bus, moving away from agent-level dispatch |
| `4be36bfc` | Various | Decoupled WebUI runtime state via typed events | Further divergence of the event/state architecture from the bus-centric model |
| `977ca725` | Various | Unified code formatting and import order | Surface-level churn that causes merge conflicts across the codebase |

Additionally, `main` accumulated **dozens of non-bus commits** (cron automation, session management, channel features) that `feat/agent-colab` either lacks or has in different forms.

## Branch Architecture

```
main  ──→  simplified in-process bus, no agent-collab
  │
  └── merge/agent-colab  (protected merge branch)
        │   Preserves: NATS, Redis, ZMQ backends, BusConfig, factory,
        │              agent_id, publish_agent_message, cross-agent routing
        │   Gets from main: bugfixes, webui, docs, non-bus features
        │
        └── feat/agent-colab  (development branch)
              ~90 files changed, ~11,500+ insertions
              Includes: .auto-resolution/ (upstream merged project)
```

## Merge Strategy (`merge/agent-colab`)

The branch `merge/agent-colab` acts as a long-lived protected merge branch that:

1. **Keeps the full bus** (from `feat/agent-colab`) as "ours" during conflict resolution
2. **Merges `main`'s non-bus improvements** — bugfixes, docs, webui, channel plugins
3. **Uses `--no-commit --no-ff`** to stage merges without auto-committing
4. **Checks `git diff --cached -- path/to/bus/`** to verify feature files survived intact
5. **Resolves conflicts with a keep-ours strategy** for all bus-related regions

### Conflict Resolution Patterns

| File | Ours (`merge/agent-colab`) | Theirs (`main`) | Resolution |
|------|---------------------------|-----------------|------------|
| `agent/loop.py` | Agent collaboration + cross-agent messaging, `CancelledError` handling, `|bot-name>` routing | Simplified loop, runtime events extracted | **Keep HEAD (ours)** |
| `cli/commands.py` | `@app.command() def gateway(...)` wrapper | Direct `def _run_gateway(...)` | **Keep ours**; combine shutdown logic from both |
| `bus/queue.py` | Full pub/sub (`subscribe_outbound`, `dispatch_outbound`) | Simplified queue, pub/sub removed | **Keep ours** (auto‑merge preserves if only our branch modified relative to merge base) |
| `tests/` | Tests from both branches | Different imports per branch | **Combine imports from both** |

### Post-Merge Structural Bugs Found

After resolving conflicts, these bugs were manually fixed:

1. **Orphaned `try`/`finally`** — a merge kept `try:` from one branch and `finally:` from another without the corresponding block, causing a syntax error
2. **Misindented dispatch logic** — fallthrough code (the "normal" case) got indented into an `if` condition during merge, causing new sessions to never reach the dispatcher

Both were fixed by manual control-flow tracing after merge.

## Files Against Which Mainline Is Diverged

The following files exist in full form on `feat/agent-colab` but differ structurally (or may conflict badly) when merging `main`:

- `nanobot/bus/__init__.py` — exposes `BusConfig`, `create_bus`, backends
- `nanobot/bus/config.py` — `BusConfig` dataclass with backend selection, agent IDs
- `nanobot/bus/factory.py` — multi-backend factory (NATS/Redis/ZMQ/local)
- `nanobot/bus/queue.py` — `MessageBus` with pub/sub + `publish_agent_message` / `consume_agent_message`
- `nanobot/bus/nats.py` — NATS backend with agent subject routing
- `nanobot/bus/redis.py` — Redis pub/sub backend (rewritten from Streams)
- `nanobot/bus/zmq.py` — ZMQ backend
- `nanobot/agent/loop.py` — agent collaboration hooks, target routing, reply/forward
- `nanobot/config/schema.py` — bus config fields (`agent_id`, `channel_prefix`, etc.)
- `nanobot/cli/commands.py` — gateway CLI entry point

## Design Philosophy

Per the design doc (`.agent/design.md`), the decision to leave agent-collab on a separate branch is consistent with the stated philosophy:

> **"Core stays small; extend at the edges."**

Multi-agent messaging is considered a feature that should live at the edges (skills, MCP servers), not in the core message bus.
