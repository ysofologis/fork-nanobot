# Custom Features — YS Nanobot

This document catalogs three custom features implemented on the
`ys-nanobot/improvements` branch.  Use it as a **merge-safety reference**
when rebasing or merging from upstream `main`.

---

## Feature 1: Channel-Specific System Prompts

**Branch:** `feat/channel-prompt`
**Status:** Merged into `ys-nanobot/improvements`

### What it does

Allows per-channel system prompt files under
`{workspace}/prompts/system_prompts/channel_{name}.md`.  The channel prompt
is injected **after** the identity/runtime block but **before** AGENTS.md /
SOUL.md / USER.md, so it acts as an authoritative persona override per
channel.

### Files changed

| File | Change | Merge risk |
|------|--------|------------|
| `nanobot/agent/context.py` | Added `_load_channel_prompt()` static method; one call in `build_system_prompt()` | Medium — if `build_system_prompt()` is refactored, the call site must be moved |
| `nanobot/command/builtin.py` | Channel name extracted from session key in `cmd_model()` | Low — isolated to one helper |

### Key code to preserve

**`nanobot/agent/context.py` — in `build_system_prompt()` (~line 85):**
```python
# Channel-specific prompt loaded before AGENTS.md et al.
channel_prompt = self._load_channel_prompt(channel, root)
if channel_prompt:
    parts.append(channel_prompt)
```

**`nanobot/agent/context.py` — new static method (~line 204):**
```python
@staticmethod
def _load_channel_prompt(channel: str | None, workspace: Path) -> str:
    if not channel:
        return ""
    prompt_path = workspace / "prompts" / "system_prompts" / f"channel_{channel}.md"
    if prompt_path.is_file():
        content = prompt_path.read_text(encoding="utf-8").strip()
        if content:
            return f"## Channel Prompt ({channel})\n\n{content}"
    return ""
```

**`nanobot/command/builtin.py` — channel line in `_model_command_status()` (~line 434):**
```python
if session_key and ":" in session_key:
    channel_name = session_key.split(":", 1)[0]
    lines.insert(1, f"- Channel: `{channel_name}`")
```

### Merge guard

Search for `_load_channel_prompt` — if missing from `context.py`, re-add both
the method and the call site inside `build_system_prompt()` (right after the
identity section, before the bootstrap files section).

---

## Feature 2: Session Pack — Topic-based History Packaging

**Branch:** `feat/perf-improvements` → `feat/session-pack`
**Status:** Merged into `ys-nanobot/improvements`

### What it does

Introduces a `channel:topic#NN` session key convention that groups related
sessions into topic-based *packs*.  Packs live under
`{workspace}/sessions/packs/{topic}/` with a `pack.json` metadata file and
numbered `NN.md` session files.  Commands: `/pack`, `/pack-list`,
`/pack-search`, `/pack-summarize`.

### Files changed

| File | Change | Merge risk |
|------|--------|------------|
| `nanobot/session/pack.py` | **NEW** — `SessionPackKey` dataclass, `parse_session_key()`, `format_session_key()` | Low — entirely new file, no upstream equivalent |
| `nanobot/session/pack_manager.py` | **NEW** — `PackManager` CRUD/search/summarize | Low — entirely new file |
| `nanobot/session/__init__.py` | Added exports for `pack.py` symbols | Low — two import lines |
| `nanobot/command/pack_cmds.py` | **NEW** — `/pack`, `/pack-list`, `/pack-search`, `/pack-summarize` handlers | Low — entirely new file |
| `nanobot/command/builtin.py` | Import `parse_session_key`; pack section in `/status`; command registration | Medium — watch the registration block in `register_builtin_commands()` |

### Key code to preserve

**`nanobot/command/builtin.py` — import (~line 17):**
```python
from nanobot.session.pack import parse_session_key
```

**`nanobot/command/builtin.py` — `/status` pack section (~lines 319–363):**
```python
# Check for session pack info
pack_section = ""
with suppress(Exception):
    pack_key = parse_session_key(ctx.key)
    if pack_key.index:
        workspace: Path | None = ...
        if workspace is not None:
            pm = PackManager(workspace)
            meta = pm.get_pack(pack_key.session_name)
            ...
            pack_section = "\n📦 Session Pack" ...
if pack_section:
    status_content += pack_section
```

**`nanobot/command/builtin.py` — command registration (~line 1130–1138):
```python
# Session pack commands (lazy import to avoid circular dependencies)
from nanobot.command.pack_cmds import cmd_pack, cmd_pack_list, cmd_pack_search
router.exact("/pack", cmd_pack)
router.prefix("/pack ", cmd_pack)
router.exact("/pack-list", cmd_pack_list)
router.exact("/pack-search", cmd_pack_search)
router.prefix("/pack-search ", cmd_pack_search)
```

### Merge guard

After rebase, verify:

1. `nanobot/session/pack.py` and `nanobot/session/pack_manager.py` exist.
2. `from nanobot.session.pack import parse_session_key` is in `builtin.py`.
3. The four `/pack*` command registrations are in `register_builtin_commands()`.
4. The `/status` pack section block is present in `cmd_status()`.

If upstream added new registrations in `register_builtin_commands()`, make sure
the session pack lines are **appended after** them, not lost.

---

## Feature 3: CLI Cancel/Interrupt — Escape cancels turn, Ctrl+C force-exits

**Branch:** `feat/cli-cancel-interrupt`
**Status:** Merged into `ys-nanobot/improvements`

### What it does

During interactive (`nanobot agent`) mode, while the agent is processing:

- **Escape** → publishes `/stop` on the bus, cancelling the current turn
- **Ctrl+C** → sets shutdown flag, force-exits the app

Uses an **input-raw** background thread (preserves OPOST for proper `\n`→`\r\n`
translation) that reads stdin via `select.poll()`.

### Files changed

| File | Change | Merge risk |
|------|--------|------------|
| `nanobot/cli/input_monitor.py` | **NEW** — `watch_control_keys()` function | Low — entirely new file |
| `nanobot/cli/agent.py` | Import + usage of `watch_control_keys()` in `run_interactive()` | Medium — inside the interactive loop |

### Key code to preserve

**`nanobot/cli/input_monitor.py` — entire file (~160 lines):**

The full implementation is in this file.  It's self-contained with only stdlib
dependencies (`os`, `select`, `sys`, `threading`, `termios`, `tty`).

**`nanobot/cli/agent.py` — import (~line 245):
```python
from nanobot.cli.input_monitor import watch_control_keys
```

**`nanobot/cli/agent.py` — callbacks and monitor start/stop (~lines 248–418):
```python
async def _publish_stop_command() -> None:
    ""Publish a /stop command to cancel the active turn."""
    await bus.publish_inbound(InboundMessage(
        channel=cli_channel,
        sender_id="user",
        chat_id=cli_chat_id,
        content="/stop",
    ))

def _on_escape() -> None:
    """Cancel the current turn (thread-safe)."""
    fut = asyncio.run_coroutine_thredsafe(
        _publish_stop_command(), _interactive_loop
    )
    fut.add_done_callback(lambda f: f.exception() if f.exception() else None)

def _on_ctrl_c() -> None:
    """Request app shutdown (thread-safe)."""
    shutdown_requested.set()
    turn_done.set()
    agent_loop.stop()
    monitor_stop.set()
```

And the monitor lifecycle around the `await turn_done.wait()` call:

```python
# Start the control-key monitor during processing
monitor_stop.clear()
_monitor_thread = watch_control_keys(
    on_escape=_on_escape,
    on_ctrl_c=_on_ctrl_c,
    stop_event=monitor_stop,
)

await turn_done.wait()

# Signal the monitor to stop
monitor_stop.set()
if _monitor_thread is not None:
    _monitor_thread.join(timeout=2)

if shutdown_requested.is_set():
    break
```

### Merge guard

If `nanobot/cli/agent.py`'s `run_interactive()` is refactored upstream:

1. Keep `nanobot/cli/input_monitor.py` — it's a new file and won't conflict.
2. In `run_interactive()`, ensure the three events exist: `shutdown_requested`,
   `monitor_stop`, `turn_done`.
3. Wrap `await turn_done.wait()` with the monitor start/stop block.
4. Keep the `_on_escape()` and `_on_ctrl_c()` callback definitions.
5. Preserve the `monitor_stop.set()` call in the `finally` block.

---

## Quick Conflict Checklist

| What to check | Where | Why |
|---------------|-------|-----|
| `_load_channel_prompt` | `nanobot/agent/context.py` | Channel-specific prompts |
| Channel line in `/model` | `nanobot/command/builtin.py` `_model_command_status()` | Channel display |
| `parse_session_key` import | `nanobot/command/builtin.py` | Session pack parsing |
| Pack section in `/status` | `nanobot/command/builtin.py` `cmd_status()` | Pack display |
| `/pack*` command registrations | `nanobot/command/builtin.py` `register_builtin_commands()` | Pack commands |
| `input_monitor.py` | `nanobot/cli/input_monitor.py` | Cancel/interrupt |
| `watch_control_keys` usage | `nanobot/cli/agent.py` `run_interactive()` | Cancel/interrupt |
| `session/pack.py` | `nanobot/session/pack.py` | Pack key parsing |
| `session/pack_manager.py` | `nanobot/session/pack_manager.py` | Pack CRUD |
| `command/pack_cmds.py` | `nanobot/command/pack_cmds.py` | Pack commands |