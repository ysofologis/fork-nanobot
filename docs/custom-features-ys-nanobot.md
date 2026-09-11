# Custom Features — YS Nanobot (Technical Reference)

Authoritative reference for the six custom features on `ys-nanobot/improvements`.
**Purpose:** make every future merge from upstream `main` predictable — never
reverse-engineer a conflict, never wonder if a feature survived a merge.

Each feature section is structured as:

1. **What it is & why** — motivation + design in two paragraphs.
2. **Code contracts** — function signatures, schemas (the merge surface).
3. **Files & risk** — every place a conflict can appear + severity.
4. **Recovery playbook** — what to do when a conflict appears.

The closing sections are the **merge procedure** and the **quick conflict
checklist** — run both on every merge from `main`.

---

## 0. Branch Topology (read this first)

```
origin/main (upstream, HKUDS/nanobot)
   └── …                                          ← latest main pulled in
        └── 6 feature commits (Yiannis)           ← Features 1–6
             = fork/ys-nanobot/improvements       ← canonical home
```

- **Canonical home** is `fork/ys-nanobot/improvements` (exact `latest-main +
  6 feature commits`). The local `ys-nanobot/improvements` is a fast-forward
  of it; never divergent. If they diverge, reset local to fork.
- `main` / `origin/main` **must stay untouched** — they are pure upstream.
- Legacy branches (`feat/channel-prompt`, `feat/cli-cancel-interrupt`,
  `feat/perf-improvements`) are historical; their content is already folded
  into the fork branch adapted to the refactored CLI
  (`nanobot/cli/agent.py` replaced `nanobot/cli/commands.py`). Do **not**
  cherry-pick from them.

> **Gotcha from the 2026-08-17 merge:** upstream was already merged (HEAD was
> 0 behind `origin/main`) but the features were *not* on the local branch.
> Before any merge, check **both** directions:
> `git rev-list --left-right --count HEAD...origin/main` (expect `N 0`) and
> `git log --oneline HEAD..fork/ys-nanobot/improvements` (should be empty
> after a clean fast-forward).

---

## Feature 1: Channel-Specific System Prompts

Upstream supports one static bootstrap prompt (AGENTS.md / SOUL.md / USER.md)
for all channels. This feature injects an **additional system prompt per
channel**, loaded from `{workspace}/prompts/system_prompts/channel_{channel}.md`,
acting as an authoritative persona override **before** the bootstrap files.
Missing or blank file → silent no-op (byte-identical prompt chain).

### 1.1 Code contracts

**`nanobot/agent/context.py`** — new static method + one call site in
`build_system_prompt()`:

```python
@staticmethod
def _load_channel_prompt(channel: str | None, workspace: Path) -> str:
    """Return '## Channel Prompt ({channel})\n\n{content}' or '' on no-op."""
    if not channel:
        return ""
    prompt_path = workspace / "prompts" / "system_prompts" / f"channel_{channel}.md"
    if prompt_path.is_file():
        content = prompt_path.read_text(encoding="utf-8").strip()
        if content:
            return f"## Channel Prompt ({channel})\n\n{content}"
    return ""

# Inside build_system_prompt(), between the identity part and the bootstrap
# files (parts.append(bootstrap)) block:
channel_prompt = self._load_channel_prompt(channel, root)
if channel_prompt:
    parts.append(channel_prompt)
```

**`nanobot/command/builtin.py`** — channel line in `_model_command_status()`,
inserted at index 1 of `lines` (right after the "## Model" header):

```python
if session_key and ":" in session_key:
    channel_name = session_key.split(":", 1)[0]
    lines.insert(1, f"- Channel: `{channel_name}`")
```

### 1.2 Files & risk

| File | Location | Risk |
|------|----------|------|
| `nanobot/agent/context.py` | `build_system_prompt()` call site + new static method | **Medium** — upstream actively refactors prompt assembly |
| `nanobot/command/builtin.py` | `_model_command_status()` | Low |

### 1.3 Recovery playbook

1. If `build_system_prompt()` changed, re-place the call **after** the
   identity append and **before** the bootstrap files append.
2. If `_load_channel_prompt` is missing, re-add the whole static method.
3. If model-status formatting changed, re-add the `if session_key and ":" in
   session_key:` block using `insert(1, …)`.

**Verify:** grep for `_load_channel_prompt` in `context.py` (both method +
call site must be present).

---

## Feature 2: Session Pack — Topic-based History Packaging

Upstream sessions are flat, keyed by opaque strings. This feature adds a
**pack** concept: a topic-based group of numbered sessions, addressable with
the grammar `channel:topic#NN`. Persisted under
`{workspace}/sessions/packs/{topic}/` with `pack.json` metadata + numbered
`NN.md` files. Commands: `/pack`, `/pack-list`, `/pack-search`,
`/pack-summarize`.

### 2.1 Code contracts

**`nanobot/session/pack.py`** (89 lines, frozen dataclass + 3 functions):

```python
_SESSION_KEY_RE = re.compile(
    r"^(?:(?P<channel>[a-zA-Z0-9_-]+):)?"
    r"(?P<name>[a-zA-Z0-9_/-]+?)"
    r"(?:#(?P<idx>\d+))?$"
)

@dataclass(frozen=True)
class SessionPackKey:
    channel: str | None
    session_name: str
    index: int = 0

def parse_session_key(key: str) -> SessionPackKey:        # raises ValueError
def format_session_key(pack: SessionPackKey) -> str:      # reverse; #NN zero-padded 2
def has_pack_index(key: str) -> bool                       # True if '#NN' suffix
```

Parsed forms:

| Input | channel | session_name | index |
|---|---|---|---|
| `assistant:nanobot-features#01` | `assistant` | `nanobot-features` | 1 |
| `nanobot-features#01` | `None` | `nanobot-features` | 1 |
| `assistant:nanobot-features` | `assistant` | `nanobot-features` | 0 |
| `plain-session` | `None` | `plain-session` | 0 |

**`pack.json` schema** (written by `PackManager._init_meta / store / summarize`):

```json
{
  "session_name": "topic", "channel": "assistant",
  "created": "ISO-8601", "updated": "ISO-8601",
  "session_count": 2, "indices": [1, 2],
  "summary": "…(first 500 chars)…", "keywords": [], "status": "active"
}
```

**`nanobot/session/pack_manager.py`** — `PackManager` API:

```python
class PackManager:
    def __init__(self, workspace: Path) -> None       # root = workspace/"sessions"/"packs"
    def resolve(self, key: str) -> dict                # parse, mkdir -p, read-or-init pack.json
    def store(self, content: str, key: str) -> dict    # write {idx:02d}.md, bump meta
    def get_pack(self, name: str) -> dict | None
    def list_packs(self) -> list[dict]                 # sorted by name
    def search(self, query: str) -> list[dict]         # relevance-ranked; match_type ∈ {title(0.9), keyword(0.7), summary(0.6), body(0.3)}
    def delete_pack(self, name: str) -> bool
    def summarize(self, name: str, summary_text: str = "") -> dict
    def get_summary(self, name: str) -> str | None

# Constants
PACKS_DIRNAME = "packs"; PACK_META_FILENAME = "pack.json"; SESSION_FILE_GLOB = "[0-9][0-9].md"
```

`search()` is a simple case-insensitive substring scan (no index). If upstream
adds a full-text index, this is the method to adapt.

**`nanobot/command/pack_cmds.py`** (173 lines) — handlers return `str`, not
`OutboundMessage`. Key symbols: `_pack_manager(ctx)` (resolves workspace,
returns `None` → error string), `_next_index(pm, name)`, `_format_pack_table`,
`cmd_pack`, `cmd_pack_list`, `cmd_pack_search`, `cmd_pack_summarize`.

### 2.2 Files & risk

| File | Location | Risk |
|------|----------|------|
| `nanobot/session/pack.py`, `pack_manager.py`, `command/pack_cmds.py` | new files | **None** — no upstream counterpart |
| `nanobot/session/__init__.py` | lines 4–8 (re-exports `SessionPackKey`, `format_session_key`, `has_pack_index`, `parse_session_key`, `PackManager`) | Low |
| `nanobot/command/builtin.py` | import line 17; `/status` pack section in `cmd_status()`; 4 `router.exact/prefix` lines in `register_builtin_commands()` | **Medium** — heavy upstream churn here |

### 2.3 Recovery playbook

1. **Never lose the three new files** — keep verbatim.
2. **Registration:** re-append the four pack `router.exact/prefix` lines
   **after** upstream additions in `register_builtin_commands()`.
3. **Import:** keep `from nanobot.session.pack import parse_session_key` at the
   top of `builtin.py`.
4. **`/status` block** is wrapped in `with suppress(Exception):` — keep that
   pattern so a mismatch degrades to no-op rather than crashing `/status`.

**Verify:** (1) the three new files exist; (2) import line present;
(3) four pack registrations; (4) pack_section block in `cmd_status()`.

---

## Feature 3: CLI Cancel/Interrupt — Escape cancels, Ctrl+C exits

During interactive `nanobot agent`, while the agent is generating or running
tools:

- **Escape** → cancel the current turn (equivalent to `/stop`), stay in app.
- **Ctrl+C** → force-exit the whole app from any state.

Design: a **background daemon thread** puts stdin into **input-raw mode**
(only input flags; preserves output flags so `\n → \r\n` is unaffected).
**Critical:** do **not** "simplify" to `tty.setraw()` — that clears `OPOST`
and output staggers rightward while the monitor is active.

| State | Escape | Ctrl+C |
|---|---|---|
| Processing (turn active) | monitor → `/stop` on bus → turn cancels | monitor → shutdown events → clean exit |
| At prompt (idle) | no-op | existing SIGINT handler → "Goodbye!" → exit |

### 3.1 Code contracts

**`nanobot/cli/input_monitor.py`** (160 lines, stdlib-only — `termios`
imported lazily for Windows-safe module load):

```python
def watch_control_keys(
    *,
    on_escape: Callable[[], None] | None = None,
    on_ctrl_c: Callable[[], None] | None = None,
    stop_event: asyncio.Event,
    poll: float = 0.15,
) -> threading.Thread | None:   # None if stdin is not a TTY or stop_event already set
```

- Recognizes only `\x1b` (Escape) and `\x03` (Ctrl+C); other bytes discarded.
  Detection is byte-presence per 64-byte read — callbacks may fire once per
  read containing the byte.
- Saves `termios.tcgetattr(fd)`, applies input-raw config, loops
  `select.poll()` while `not stop_event.is_set()`, restores attrs in
  `finally` (terminal safety on every exit path).
- **Callbacks run on the background thread.** Bridge to async with
  `asyncio.run_coroutine_threadsafe(coro, loop)`.

**`nanobot/cli/agent.py`** (`run_interactive()`) — wiring:
- Three `asyncio.Event`s: `shutdown_requested`, `monitor_stop`, `turn_done`.
- `_publish_stop_command()` publishes an `InboundMessage(channel=cli_channel,
  sender_id="user", chat_id=cli_chat_id, content="/stop")` on the bus.
- `_on_escape()` → `asyncio.run_coroutine_threadsafe(_publish_stop_command(),
  _interactive_loop)` + done-callback surfacing exceptions.
- `_on_ctrl_c()` → sets `shutdown_requested` + `turn_done`, calls
  `agent_loop.stop()`, sets `monitor_stop`.
- Monitor start: `monitor_stop.clear()` then `watch_control_keys(...)`.
- Wait/stop: `await turn_done.wait()` → `monitor_stop.set()` →
  `_monitor_thread.join(timeout=2)`.
- Teardown `finally:` → `monitor_stop.set()` + `agent_loop.stop()` + task
  cleanup.

### 3.2 Files & risk

| File | Location | Risk |
|------|----------|------|
| `nanobot/cli/input_monitor.py` | new file | **None** |
| `nanobot/cli/agent.py` | `run_interactive()` wiring block (events, callbacks, monitor start/stop) | **Medium** — upstream actively refactors the CLI |

### 3.3 Recovery playbook

1. Keep `input_monitor.py` verbatim — never conflicts.
2. In `run_interactive()`, ensure the **three events** exist
   (`shutdown_requested`, `monitor_stop`, `turn_done`).
3. Keep the monitor start/stop/join block wrapping the
   `await turn_done.wait()` — start **after** the inbound message is
   published, **before** the wait; stop **after** the wait resolves.
4. Keep `_on_escape` / `_on_ctrl_c` definitions and the `monitor_stop.set()`
   in the `finally:` block.
5. If upstream moves channel/chat_id derivation, `_publish_stop_command`
   must keep using `cli_channel` / `cli_chat_id`.

---

## Feature 4: Tool-Call Performance (per-tool disk latency)

When the agent workspace or sessions dir sits on a slow mount, every tool
call pays fixed disk costs (tens to hundreds of ms) that stall the event
loop:

1. **Tool-result offload writes** — results > `max_tool_result_chars`
   (default 16 000) are written to `{workspace}/.nanobot/tool-results/` via
   `_write_text_atomic()` with file + directory fsync. Slow mount:
   ~46–54 ms/write (vs 0.04 ms on `/tmp`).
2. **Runtime-checkpoint sidecars** — runner emits **two checkpoints per tool
   batch** (`awaiting_tools` + `tools_completed`), each synchronously
   serializing the whole transcript and writing it next to the session file
   on the event loop (grows quadratically with transcript size).

Three behavior-preserving fixes (safe for fast-disk setups):

1. **No-fsync offload** — regenerable cache files skip fsync.
2. **Fast local dirs** — tool-results and runtime-checkpoint sidecars
   redirect to `~/.cache/nanobot/...` (config knob + automatic default).
3. **Coalesced, off-loop checkpoint writes** — captured synchronously but
   written via `asyncio.to_thread` with per-session coalescing (identical
   payloads skipped); event loop never blocks.

### 4.1 Code contracts

**`nanobot/utils/helpers.py`:**

```python
def _write_text_atomic(path: Path, content: str, *, fsync: bool = True) -> None:
    # fsync=False skips file + directory fsync (regenerable caches only)

def maybe_persist_tool_result(
    workspace: Path | None,
    session_key: str | None,
    tool_call_id: str,
    content: Any,
    *,
    max_chars: int,
    results_dir: Path | None = None,    # NEW: overrides {workspace}/.nanobot/tool-results
) -> Any
```

**`nanobot/config/schema.py`** — `AgentDefaults`:

```python
tool_results_dir: str | None = Field(
    default=None,
    validation_alias=AliasChoices("toolResultsDir", "tool_results_dir"),
    serialization_alias="toolResultsDir",
)   # None = workspace default; relative paths resolve against the workspace
```

**`nanobot/agent/context_governance.py` / `nanobot/agent/runner.py`:**
- `ContextGovernanceConfig.tool_results_dir: Path | None = None` — passed to
  `maybe_persist_tool_result(results_dir=...)`.
- `AgentRunSpec.tool_results_dir: Path | None = None` — threaded into
  `ContextGovernanceConfig` in `AgentRunner.run()`.

**`nanobot/agent/loop.py`:**

```python
def _runtime_checkpoint_cache_dir() -> Path | None:
    # ~/.cache/nanobot/runtime-checkpoints (fallback: /tmp, then None).
    # Passed as SessionManager(checkpoint_dir=...) in create_loop().

def _schedule_checkpoint_write(self, session: Session) -> None:
    # Called from _set_runtime_checkpoint(). Captures (payload, provider_state)
    # synchronously, coalesces per-session by token, then:
    #   loop.create_task(asyncio.to_thread(_write))
    # where _write() calls sessions.save_runtime_checkpoint_snapshot(...)
```

**`nanobot/session/manager.py`:**

```python
class JsonlSessionStore:
    def __init__(self, workspace, *, sessions_root=None, checkpoint_dir=None):
        ...
        self._checkpoint_dir = ...     # None → checkpoints next to session files
    def get_runtime_checkpoint_path(self, key):     # uses _checkpoint_dir if set
    def save_runtime_checkpoint_snapshot(self, session, *, payload, provider_state):
        # Writes captured snapshot without reading the mutated session
    def _write_checkpoint_unlocked(self, session, *, checkpoint, provider_state):
        # Shared writer; caller holds _session_files_lock

class SessionManager:
    def __init__(self, workspace, *, store=None, sessions_root=None, checkpoint_dir=None):
    def save_runtime_checkpoint_snapshot(self, session, *, payload, provider_state):
        # Facade: policy.persist check → _jsonl_store delegation → _remember()
```

### 4.2 Files & risk

| File | Location | Risk |
|------|----------|------|
| `nanobot/utils/helpers.py` | `_write_text_atomic`, `maybe_persist_tool_result` | Low |
| `nanobot/config/schema.py` | `AgentDefaults.tool_results_dir` | Low (additive field) |
| `nanobot/agent/context_governance.py` | `ContextGovernanceConfig` | Low |
| `nanobot/agent/runner.py` | `AgentRunSpec` + `run()` | Low–Medium |
| `nanobot/agent/loop.py` | `_set_runtime_checkpoint`, `create_loop()` SessionManager construction | **High** — hot zone; upstream actively refactors checkpoint/recovery |
| `nanobot/session/manager.py` | `JsonlSessionStore.__init__`, `SessionManager.__init__`, checkpoint writers | **Medium–High** |

### 4.3 Recovery playbook

1. **`_write_text_atomic`** — if upstream rewrote it, re-add `fsync: bool =
   True` keyword + the two `fsync=False` calls inside `maybe_persist_tool_result`.
   If the latter changed, keep the `results_dir` kwarg and `ensure_dir(
   results_dir if results_dir is not None else workspace / _TOOL_RESULTS_DIR)`.
2. **Dataclass plumbing** — keep `tool_results_dir` on **both** `AgentRunSpec`
   and `ContextGovernanceConfig`, plus the two pass-throughs
   (`AgentRunSpec → ContextGovernanceConfig` in `run()`;
   `ContextGovernanceConfig → maybe_persist_tool_result` in
   `normalize_tool_result()`).
3. **`_schedule_checkpoint_write`** is the block upstream churn most likely
   to displace. If `_set_runtime_checkpoint` changed, re-add the coalescing
   writer verbatim (capture → token → `asyncio.to_thread` → snapshot write).
4. **Session store** — keep `checkpoint_dir` on both `JsonlSessionStore` and
   `SessionManager.__init__`, and the `_checkpoint_dir` branch in
   `get_runtime_checkpoint_path()`. Keep `save_runtime_checkpoint_snapshot`
   on both classes (`_write_checkpoint_unlocked` is the shared writer).

**Verify:** grep for `_schedule_checkpoint_write` in `loop.py` and
`save_runtime_checkpoint_snapshot` in `manager.py`; confirm
`_runtime_checkpoint_cache_dir()` is used in `create_loop()`.

---

## Feature 5: Prompt Launcher — `/prompt <name>` + `/prompt-list`

The agent workspace may hold reusable prompt snippets under
`{workspace}/prompts/` (the same folder hosting `dream.md` / `evaluator.md`
overrides). This feature launches any of them as a full agent turn:

- `/prompt <name> [extra text...]` loads `{workspace}/prompts/<name>.md`,
  replaces the inbound message content with the prompt body, and appends any
  trailing user text. The handler returns `None`, so the turn proceeds
  through the normal agent pipeline (`_build_turn` → `_run_turn`) like
  `/goal`. The loaded prompt body is what flows to the LLM **and** what
  gets persisted as history (matching `/goal`'s behavior).
- `/prompt-list` lists every non-empty `.md` under `prompts/` that is not an
  internal override (`dream`, `evaluator`) and not inside
  `system_prompts/` (Feature 1).

### 5.1 Code contracts

**`nanobot/command/prompt_cmds.py`** (new, ~180 lines):

```python
async def cmd_prompt(ctx: CommandContext) -> OutboundMessage | None:
    """``/prompt <name> [extra text...]`` — launch a saved prompt as an agent turn."""
    # - resolves workspace via ctx.loop.workspace (falls back to ctx.loop.context.workspace)
    # - loads prompts/<name>.md via load_workspace_prompt_override()
    # - rejects names containing '/' or '\\' (path traversal guard)
    # - sets ctx.msg.metadata['prompt_launched'] = name
    # - sets ctx.msg.content = prompt_body (+ '\n\n' + extra if provided)
    # - returns None → normal agent turn proceeds with the loaded prompt

async def cmd_prompt_list(ctx: CommandContext) -> OutboundMessage:
    """``/prompt-list`` — list all launchable prompts under prompts/."""
    # - skips empty files, internal overrides (dream/evaluator), system_prompts/
    # - one line per prompt: `name` (N chars) — first line preview

_NON_LAUNCHABLE_PROMPTS = {"dream", "evaluator"}
_NON_LAUNCHABLE_SUBDIRS = {"system_prompts"}
```

**`nanobot/command/builtin.py`** — registration & specs (both blocks right
before the pack block):

```python
# In BUILTIN_COMMAND_SPECS:
BuiltinCommandSpec("/prompt", "Launch prompt",
    "Launch a saved prompt from prompts/<name>.md as an agent turn.",
    "sparkles", "<name> [extra text...]",
    lifecycle="agent_turn_with_args", accepts_args=True),
BuiltinCommandSpec("/prompt-list", "List prompts",
    "List all launchable prompts under the workspace prompts folder.",
    "list"),

# In register_builtin_commands():
from nanobot.command.prompt_cmds import cmd_prompt, cmd_prompt_list
router.prefix("/prompt ", cmd_prompt)
router.exact("/prompt", cmd_prompt)
router.exact("/prompt-list", cmd_prompt_list)
```

`lifecycle="agent_turn_with_args"` makes WebUI treat `/prompt <name>` as a
normal agent turn (like `/goal`); bare `/prompt` stays side-channel.

### 5.2 Files & risk

| File | Location | Risk |
|------|----------|------|
| `nanobot/command/prompt_cmds.py` | new file | **None** |
| `nanobot/command/builtin.py` | `BUILTIN_COMMAND_SPECS` + `register_builtin_commands()` | Low |
| `nanobot/utils/workspace_prompts.py` | `load_workspace_prompt_override` signature | Low |

### 5.3 Recovery playbook

1. If `register_builtin_commands()` changed, re-add the three router lines
   (`/prompt ` prefix, `/prompt` exact, `/prompt-list` exact) — keep right
   before the session-pack block.
2. If `BUILTIN_COMMAND_SPECS` churned, re-add the two specs verbatim.
3. If `load_workspace_prompt_override` signature changed, adapt the call in
   `cmd_prompt` / `cmd_prompt_list`.

**Verify:** grep for `cmd_prompt` in `prompt_cmds.py` and `builtin.py`.

---

## Feature 6: Async-Fire-and-Forget Session Save

The trailing `sessions.save(session)` at the end of every turn is scheduled
as a background task instead of awaited inline. The user sees the response
the moment `_persist_turn` finishes; the JSON flush completes a few ms
later on the event loop. Durability is preserved by:

1. `aclose()` already awaits `_background_tasks` on shutdown.
2. `SessionManager._session_files_lock` (atomic-rename) still serializes
   concurrent disk writes.
3. New `await_pending_session_saves()` helper for tests + callers that need
   a strong durability boundary before reading the session back from disk.

Per-turn latency is dominated by the LLM stream, but the trailing disk write
can be tens of ms on slow mounts (NFS, network drives, USB). Measured on a
synthetic 30 ms-write workload: **122.92 ms → 78.64 ms per turn (1.6×,
44 ms win)**.

### 6.1 Code contracts

**`nanobot/agent/loop.py`** — two new helpers + one call-site change:

```python
def schedule_session_save(self, session: Session) -> None:
    """Persist a session to disk without blocking the current turn.

    Session captured by reference: in-memory state is authoritative; the
    per-session asyncio lock prevents a racing turn for the same key from
    interleaving. Crash-safety preserved by SessionManager._session_files_lock
    (atomic-rename) and by awaiting pending saves in aclose().
    """
    async def _flush() -> None:
        try:
            result = self.sessions.save(session)
            if inspect.isawaitable(result):
                await result
        except Exception:
            logger.exception("Background session save failed for {}", session.key)
    self.schedule_background(_flush())

async def await_pending_session_saves(self) -> None:
    """Wait for any in-flight background session saves to complete."""
    if self._background_tasks:
        await asyncio.gather(*tuple(self._background_tasks), return_exceptions=True)

# In _persist_turn() (replaces the previous self.sessions.save(session)):
self.schedule_session_save(session)
```

### 6.2 Files & risk

| File | Location | Risk |
|------|----------|------|
| `nanobot/agent/loop.py` | new helpers (lines ~1636, ~1662) + call-site in `_persist_turn` (line ~2142) | **Low** — additive, isolated to one function |

### 6.3 Recovery playbook

1. If upstream removed the trailing `sessions.save(session)` entirely, **add
   it back via `self.schedule_session_save(session)`**, not via direct call.
2. If upstream added a new stage before `_persist_turn` that also calls
   `self.sessions.save(session)`, leave it alone if it's in the
   `_build_turn` / early-restore path (no user-facing response yet — sync is
   fine), but **convert any save at the end of `_run_turn` or
   `_prepare_outbound` to `schedule_session_save`**.

**Recovery-path save is intentionally sync.** The cancellation handler in
`AgentLoop.run()` (the `/stop` flow) still calls `self.sessions.save(session)`
inline — `/stop` must materialize partial context immediately so the next
prompt sees completed tool results; deferring that save would lose state on a
fast subsequent shutdown.

**Verify:** grep for `schedule_session_save` (helper + call site) and
`await_pending_session_saves` in `loop.py`.

---

## Merge Procedure (from `main`)

Run this exact sequence every time. Designed so even a chaotic upstream
merge cannot silently drop a feature.

```bash
# 1. Snapshot & fetch
git status --porcelain                  # must be clean; stash if not
git tag "safety/pre-merge-$(date +%Y%m%d-%H%M%S)" HEAD
git fetch origin main                   # upstream HKUDS/nanobot

# 2. Pre-merge state check (both directions!)
git rev-list --left-right --count HEAD...origin/main   # expect "N 0"
git log --oneline HEAD..fork/ys-nanobot/improvements   # expect EMPTY
git merge-base --is-ancestor origin/main HEAD && echo "main fully merged" || echo "MUST MERGE main"

# 3. If upstream has new commits, merge it
git merge origin/main                   # resolve conflicts using the per-feature playbooks

# 4. Re-apply features if anything was lost (see checklist below); confirm:
git log --oneline HEAD..fork/ys-nanobot/improvements   # should be empty → features intact
```

If the fork branch is ahead of HEAD (features missing locally), fast-forward:

```bash
git merge --ff-only fork/ys-nanobot/improvements
```

**Never** force-push `ys-nanobot/improvements` unless you own the remote copy
(`git push --force-with-lease`, never `--force`). Do **not** rewrite the
local `main` / `origin/main`.

---

## Quick Conflict Checklist

After any merge/rebase, confirm every row:

| What to check | Where | Why |
|---------------|-------|-----|
| `_load_channel_prompt` method + call site | `nanobot/agent/context.py` | F1 — channel prompts |
| Channel line in `/model` | `nanobot/command/builtin.py` `_model_command_status()` | F1 — channel display |
| `parse_session_key` import | `nanobot/command/builtin.py` (line 17) | F2 — pack parsing |
| Pack section in `/status` | `nanobot/command/builtin.py` `cmd_status()` (321–362) | F2 — pack display |
| Four `/pack*` registrations | `nanobot/command/builtin.py` `register_builtin_commands()` (1143–1151) | F2 — pack commands |
| `input_monitor.py` | `nanobot/cli/input_monitor.py` | F3 — cancel/interrupt |
| `watch_control_keys` usage + 3 events | `nanobot/cli/agent.py` `run_interactive()` | F3 — cancel/interrupt wiring |
| `session/pack.py`, `session/pack_manager.py`, `command/pack_cmds.py` | full files | F2 — pack CRUD + commands |
| `session/__init__.py` exports | `nanobot/session/__init__.py` (lines 4–8) | F2 — public API |
| `_write_text_atomic` `fsync` kwarg + `results_dir` | `nanobot/utils/helpers.py` | F4 — no-fsync offload |
| `toolResultsDir` config | `nanobot/config/schema.py` `AgentDefaults` | F4 — fast local offload dir |
| `_schedule_checkpoint_write` + `_runtime_checkpoint_cache_dir` | `nanobot/agent/loop.py` | F4 — coalesced off-loop checkpoints |
| `save_runtime_checkpoint_snapshot` + `checkpoint_dir` | `nanobot/session/manager.py` | F4 — snapshot writer + fast checkpoint dir |
| `prompt_cmds.py` | `nanobot/command/prompt_cmds.py` | F5 — prompt launcher |
| Two `/prompt` + `/prompt-list` specs | `nanobot/command/builtin.py` `BUILTIN_COMMAND_SPECS` | F5 — palette/help |
| Three prompt router lines | `nanobot/command/builtin.py` `register_builtin_commands()` (before pack block) | F5 — dispatch |
| `schedule_session_save` + `await_pending_session_saves` | `nanobot/agent/loop.py` | F6 — async-save helpers |
| `self.schedule_session_save(session)` in `_persist_turn` | `nanobot/agent/loop.py` (~line 2142) | F6 — call site |

---

## Post-Merge Verification

```bash
# AST parse all feature-touched files (fast smoke test)
python3 - <<'EOF'
import ast
files = [
    "nanobot/agent/context.py", "nanobot/cli/agent.py",
    "nanobot/cli/input_monitor.py", "nanobot/command/builtin.py",
    "nanobot/command/pack_cmds.py", "nanobot/session/__init__.py",
    "nanobot/session/pack.py", "nanobot/session/pack_manager.py",
    "nanobot/utils/helpers.py", "nanobot/session/manager.py",
    "nanobot/agent/context_governance.py", "nanobot/agent/runner.py",
    "nanobot/agent/loop.py", "nanobot/config/schema.py",
    "nanobot/command/prompt_cmds.py",
]
for f in files:
    ast.parse(open(f).read()); print(f"OK  {f}")
EOF

# Imports resolve
uv run --no-sync python -c \
  "from nanobot.session.pack import SessionPackKey, parse_session_key; \
   from nanobot.session.pack_manager import PackManager; \
   from nanobot.cli.input_monitor import watch_control_keys; \
   from nanobot.agent.context import ContextBuilder; \
   from nanobot.session.manager import SessionManager; \
   from nanobot.utils.helpers import _write_text_atomic, maybe_persist_tool_result; \
   from nanobot.command.prompt_cmds import cmd_prompt, cmd_prompt_list; print('imports OK')"

# Feature tests
python3 -m pytest tests/command/test_prompt_commands.py -q
python3 -m pytest tests/agent/test_async_save.py -q

# Full gate (matches CI)
ruff check nanobot/
uv run --no-sync basedpyright

# Functional smoke (manual): in `nanobot agent`, during a long turn press
# Escape (turn cancels, app stays) and Ctrl+C (clean "Goodbye!" exit).
# Perf sanity: a large tool result (>16k chars) should not stall the loop;
# check {workspace}/.nanobot/tool-results or the configured toolResultsDir
# receives the offload file with no fsync delay.
```

---

*Maintained as part of the `nanobot-improvements` task
(`user-tasks/nanobot-improvements/USER-TASK.md`). Update this document whenever
a feature's code contracts change — an outdated merge reference is worse than
none.*
