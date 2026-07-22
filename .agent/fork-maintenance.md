# Fork Maintenance — Staying in Sync with Upstream

> **Purpose**: Practical checklist for merging upstream nanobot changes while keeping custom features (agent-collab, NATS bus, etc.) intact.

## Ground Rules

1. **Custom code lives in `nanobot/ext/`** — zero upstream overlap, never conflicts.
2. **Upstream files get only thin delegation calls** — the actual logic stays in `ext/`. This limits merge conflicts to a handful of files.
3. **New bus backends (`nanobot/bus/nats.py`)** — if upstream refactors the bus interface, you need to update the backend signature. Check `nanobot/bus/factory.py` and `nanobot/bus/config.py`.

## Merge Procedure

```bash
# 1. Fetch upstream
git fetch upstream main

# 2. Merge (not rebase — too many conflicts on a fork)
git merge upstream/main

# 3. Run the smoke test
python -c "from nanobot.config.schema import Config; print('OK')"
python -c "from nanobot.agent.loop import AgentLoop; print('OK')"
python -c "from nanobot.optional_features import optional_features_payload; print('OK')"
python -c "from nanobot.ext.agent_collab import parse_agent_route; print('OK')"
```

## Known Failure Points After Merge

### 1. Circular Import in Tool Config Loading

**Symptom**: `ImportError` or `AttributeError` when importing `Config` — traceback goes through `_resolve_tool_config_refs()` then into a tool module that imports from `nanobot.config.paths`.

**Why**: `config/schema.py` calls `_resolve_tool_config_refs()` at module level (line ~663), which eagerly imports tool config classes. If any of those modules do a module-level `from nanobot.config.paths import ...`, it creates a cycle because `config.paths` → `config.__init__` → `config.loader` → `config.schema`.

**Fix**: Move the `config.paths` import into the function body (lazy import):

```python
# BROKEN (module-level):
from nanobot.config.paths import get_media_dir

# FIXED (inside the method that uses it):
def my_method(self):
    from nanobot.config.paths import get_media_dir
    ...
```

**Checklist after fixing**: Search for any newly-added module-level imports from `nanobot.config.paths` or `nanobot.config` inside tool modules under `nanobot/agent/tools/`.

### 2. Loop Corrupted by Merge Conflict Resolution

**Symptom**: `SyntaxError` on `asyncio.CancelledError` or `NameError: name 'clean' is not defined` in `nanobot/agent/loop.py`.

**Why**: The `run()` method in `loop.py` has the agent-collab routing code spliced into upstream's message consumption loop. When both sides modify the same `try/except` block, the merge can produce:

- `except` blocks at wrong indentation
- Variables from one side referenced on the other side of a conflict marker
- Missing `except Exception` catch-all

**The block structure should look like**:

```python
while self._running:
    try:
        msg = await asyncio.wait_for(self.bus.consume_inbound(), timeout=1.0)
    except asyncio.TimeoutError:
        # agent-collab: check inbound agent-to-agent messages
        agent_msg = await receive_from_agent(self.bus)
        ...  # routing logic
    except asyncio.CancelledError:
        ...  # cancellation handling
    except Exception as e:
        ...  # catch-all

    raw = msg.content.strip()
    ...

    # agent-collab: |bot-name> routing
    target_agent, clean = parse_agent_route(raw)
    if target_agent:
        if target_agent != self.bus.agent_id:
            await forward_to_target_agent(...)
            continue
        raw = clean

    ...  # normal processing
```

**Fix**: Restore the correct structure from `.agent/agent-collab-refactoring.md` or the `merge/agent-colab` branch reference.

### 3. Bus Interface Changes

**Symptom**: `AgentLoop` fails to instantiate with `TypeError` — missing `agent_id` argument or new required parameter.

**Why**: Upstream may refactor the `MessageBus` interface. The agent-collab code depends on `bus.agent_id`, `bus.publish_agent_message()`, and `bus.consume_agent_message()`.

**Fix**: Check `nanobot/bus/base.py` and `nanobot/bus/queue.py` for interface changes. Update `nanobot/bus/nats.py` to match.

## Files That Need Review After Every Merge

| File | What to Check |
|---|---|
| `nanobot/agent/loop.py` | The `run()` method — `try/except` structure, agent-collab routing calls |
| `nanobot/config/schema.py` | `_resolve_tool_config_refs()` — any new tool config imports added by upstream |
| `nanobot/agent/tools/*.py` | New tool modules may import from `config.paths` at module level — move to lazy |
| `nanobot/bus/queue.py` | Bus interface changes (constructor, consume_inbound, etc.) |
| `nanobot/bus/events.py` | Event dataclass changes |
| `nanobot/bus/factory.py` | NATS backend registration |

## Quick Smoke Test

```bash
python -c "
from nanobot.config.schema import Config
from nanobot.agent.loop import AgentLoop
from nanobot.bus.nats import NatsBus
from nanobot.ext.agent_collab import parse_agent_route, forward_to_target_agent
print('All imports OK — merge is clean')
"
```
