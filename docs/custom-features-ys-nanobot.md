# Custom Features — YS Nanobot (Technical Reference)

This document is the authoritative technical reference for the four custom
features implemented on the `ys-nanobot/improvements` branch. It exists for
one purpose: **make every future merge/rebase from upstream `main` predictable
and painless** — you should never have to reverse-engineer a conflict, wonder
whether a feature survived a merge, or rediscover where a feature hooks in.

Use it as a merge playbook, not just a catalog. Each feature section lists:

1. **Motivation & design** — why the feature exists and how it works.
2. **Exact code contracts** — function signatures, data structures, on-disk
   schemas (accurate to the current tree).
3. **Integration points** — the exact spots where the feature touches upstream
   code (these are the only places conflicts can occur).
4. **Merge risk** — where upstream churn is likely, and how to react.
5. **Conflict-resolution playbook** — what to do when a conflict appears.

The last two sections are the **step-by-step merge procedure** and the
**quick conflict checklist** — run through both on every merge from `main`.

---

## 0. Branch Topology (read this first)

```
origin/main (upstream, HKUDS/nanobot)
   └── b397409e "Merge branch 'main'"      ← latest main pulled in
        └── dce0b7e4 Enable channel prompt       ┐
        └── 0cab9aca Enable session packing      │ 6 feature commits
        └── 4e35a71e Fix chat interrupt keys     │ (authored by Yiannis)
        └── ce741ce6 Apply CLI monitor to agent.py
        └── be5e1b74 Add /pack-summarize ...
        └── 21fdb386 docs: merge-safety reference
        └── perf(tools): cut per-tool-call disk latency   ← Feature 4 (perf)
             = fork/ys-nanobot/improvements  ← current HEAD (ys-nanobot/improvements)
```

- The canonical home of these features is the **fork remote** branch
  `fork/ys-nanobot/improvements` — it is exactly `latest-main + 7 feature
  commits` (6 original + the perf feature).
- The local `ys-nanobot/improvements` branch **fast-forwards** onto it (a pure
  fast-forward; never a divergent merge). If they ever diverge, the fork branch
  wins and the local branch should be reset to it.
- The local `main` branch and `origin/main` **must stay untouched** — they are
  pure upstream. All feature work lives on `ys-nanobot/improvements`.
- Legacy feature branches (`feat/channel-prompt`, `feat/cli-cancel-interrupt`,
  `feat/perf-improvements`) are **historical only**; their content is already
  folded into the fork branch, adapted to the refactored CLI
  (`nanobot/cli/agent.py` replaced `nanobot/cli/commands.py`). Do not cherry-pick
  from them — use the fork branch as the single source of truth.

> **Known gotcha from the 2026-08-17 merge:** upstream `main` was *already*
> merged (HEAD was 0 behind `origin/main`) but the features were **not** on the
> local branch. The "merge" was actually a fast-forward to `fork/ys-nanobot/
> improvements`. Before any merge, check **both** directions:
> `git rev-list --left-right --count HEAD...origin/main` (expect `N 0`) and
> confirm the features exist (`git log --oneline HEAD..fork/ys-nanobot/improvements`
> should be empty after the fast-forward).

---

## Feature 1: Channel-Specific System Prompts

**Status:** Merged into `ys-nanobot/improvements` (commit `dce0b7e4`)

### 1.1 Motivation & design

Upstream only supports one static bootstrap prompt set (AGENTS.md / SOUL.md /
USER.md) for all channels. This feature lets each channel (assistant, telegram,
discord, slack, …) inject an **additional system prompt** that acts as an
authoritative persona override **before** the workspace bootstrap files.

Channel prompts are plain Markdown files at:

```
{workspace}/prompts/system_prompts/channel_{channel_name}.md
```

Prompt insertion order (top → bottom):

```
Runtime identity → Workspace → Platform Policy
  → Channel Prompt ({channel})      ← NEW (only if the file exists)
  → AGENTS.md → SOUL.md → USER.md
  → Tool Contract → Memory / Skills / Recent History / Summary
```

If the file is missing or blank, the feature is a **silent no-op** — the prompt
chain is byte-identical to upstream.

### 1.2 Exact code contracts

**`nanobot/agent/context.py` — new static method (line 204):**

```python
@staticmethod
def _load_channel_prompt(channel: str | None, workspace: Path) -> str:
    """Load {workspace}/prompts/system_prompts/channel_{channel}.md
    wrapped in a '## Channel Prompt ({channel})' header, or '' if
    channel is None, the file is missing, or the file is blank."""
    if not channel:
        return ""
    prompt_path = workspace / "prompts" / "system_prompts" / f"channel_{channel}.md"
    if prompt_path.is_file():
        content = prompt_path.read_text(encoding="utf-8").strip()
        if content:
            return f"## Channel Prompt ({channel})\n\n{content}"
    return ""
```

**`nanobot/agent/context.py` — call site inside `build_system_prompt()` (lines 84–87):**

```python
# Channel-specific prompt loaded before AGENTS.md et al.
channel_prompt = self._load_channel_prompt(channel, root)
if channel_prompt:
    parts.append(channel_prompt)
```

`channel` flows in as the `channel: str | None` keyword of
`build_system_prompt()` (already existed upstream — the feature only *uses* it).

**`nanobot/command/builtin.py` — channel line in `_model_command_status()` (lines 432–434):**

```python
if session_key and ":" in session_key:
    channel_name = session_key.split(":", 1)[0]
    lines.insert(1, f"- Channel: `{channel_name}`")
```

`cmd_model()` (line 439) already passed `session_key=ctx.key` — this was the
only signature change needed; the `session_key` parameter of
`_model_command_status()` is optional, so all other callers are untouched.

### 1.3 Integration points (only places conflicts can occur)

| File | Location | What conflicts |
|------|----------|----------------|
| `nanobot/agent/context.py` | `build_system_prompt()` lines 84–87 | Upstream refactor of prompt assembly |
| `nanobot/agent/context.py` | new method at line 204 | Collision only if upstream adds a same-named method |
| `nanobot/command/builtin.py` | `_model_command_status()` lines 432–434 | Upstream rewrites model status formatting |

### 1.4 Merge risk

- **Low–Medium.** The two touch points are small and isolated.
- **Highest risk:** upstream refactoring `build_system_prompt()` (e.g. changing
  `parts` handling, moving bootstrap loading, or changing the identity call).
- `_load_channel_prompt` itself is static and self-contained — it survives any
  upstream change as long as the call site is re-placed correctly.

### 1.5 Conflict-resolution playbook

1. If `build_system_prompt()` changed: re-locate the call site. It must sit
   **after** the identity part is appended and **before** the bootstrap files
   part (`_load_bootstrap_files` / `parts.append(bootstrap)`).
2. If `_load_channel_prompt` is missing: re-add the whole static method verbatim.
3. If the model-status code changed: re-add the `if session_key and ":" in
   session_key:` block, keeping `insert(1, ...)` so the Channel line renders
   right after the "## Model" header.
4. Verify with the checklist in §6 (commands) and the merge guard:

```
Search for _load_channel_prompt in nanobot/agent/context.py.
If missing → re-add the method AND the call site in build_system_prompt().
```

---

## Feature 2: Session Pack — Topic-based History Packaging

**Status:** Merged into `ys-nanobot/improvements` (commits `0cab9aca`, `be5e1b74`)

### 2.1 Motivation & design

Upstream sessions are flat, keyed by opaque session keys. This feature
introduces a **pack** concept: a topic-based group of numbered sessions,
addressable with the key grammar `channel:topic#NN`.

Packs persist on disk under:

```
{workspace}/sessions/packs/{topic}/
├── pack.json        # metadata (see schema below)
├── summary.md       # written by /pack-summarize
├── 01.md            # numbered session files (SESSION_FILE_GLOB)
└── 02.md
```

Commands added: `/pack <topic>`, `/pack-list`, `/pack-search <q>`,
`/pack-summarize [<topic>]`.

### 2.2 Session key grammar (the contract everything hinges on)

**`nanobot/session/pack.py` — `_SESSION_KEY_RE` (lines 27–31):**

```python
_SESSION_KEY_RE = re.compile(
    r"^(?:(?P<channel>[a-zA-Z0-9_-]+):)?"
    r"(?P<name>[a-zA-Z0-9_/-]+?)"
    r"(?:#(?P<idx>\d+))?$"
)
```

Parsed forms (from `parse_session_key` docstring):

| Input | `channel` | `session_name` | `index` |
|---|---|---|---|
| `assistant:nanobot-features#01` | `assistant` | `nanobot-features` | 1 |
| `nanobot-features#01` | `None` | `nanobot-features` | 1 |
| `assistant:nanobot-features` | `assistant` | `nanobot-features` | 0 |
| `plain-session` | `None` | `plain-session` | 0 |

**Public API of `nanobot/session/pack.py` (89 lines, frozen dataclass):**

```python
@dataclass(frozen=True)
class SessionPackKey:
    channel: str | None
    session_name: str
    index: int = 0

def parse_session_key(key: str) -> SessionPackKey:   # raises ValueError on no match
def format_session_key(pack: SessionPackKey) -> str: # reverse; #NN zero-padded 2
def has_pack_index(key: str) -> bool                 # True if '#NN' suffix present
```

### 2.3 On-disk metadata schema (`pack.json`)

Written by `PackManager._init_meta()` / `store()` / `summarize()`:

```json
{
  "session_name": "topic",
  "channel": "assistant",
  "created": "2026-07-29T00:58:33+00:00",
  "updated": "2026-07-29T00:58:33+00:00",
  "session_count": 2,
  "indices": [1, 2],
  "summary": "…(first 500 chars of summary)…",
  "keywords": [],
  "status": "active"
}
```

### 2.4 `PackManager` API contract (`nanobot/session/pack_manager.py`)

```python
class PackManager:
    def __init__(self, workspace: Path) -> None:        # root = workspace/"sessions"/"packs"
    def resolve(self, key: str) -> dict[str, Any]:       # parse, mkdir -p, read-or-init pack.json
    def store(self, content: str, key: str) -> dict[str, Any]:  # write {idx:02d}.md, bump meta
    def get_pack(self, name: str) -> dict[str, Any] | None:     # read pack.json (+session_name)
    def list_packs(self) -> list[dict[str, Any]]:        # sorted by name
    def search(self, query: str) -> list[dict[str, Any]]:# relevance-ranked, see below
    def delete_pack(self, name: str) -> bool:            # shutil.rmtree
    def summarize(self, name: str, summary_text: str = "") -> dict[str, Any]:  # writes summary.md
    def get_summary(self, name: str) -> str | None
```

Constants: `PACKS_DIRNAME = "packs"`, `PACK_META_FILENAME = "pack.json"`,
`SESSION_FILE_GLOB = "[0-9][0-9].md"`.

`search()` returns dicts of shape
`{"session_name", "match_type", "snippet", "score"}` with `match_type` in
`{"title" (0.9), "keyword" (0.7), "summary" (0.6), "body" (0.3)}`, sorted by
score descending. It is a **simple case-insensitive substring scan** (no
indexing) — if upstream ever adds a full-text index, this is the method to
adapt.

### 2.5 Command handlers (`nanobot/command/pack_cmds.py`, 173 lines)

| Symbol | Line | Behavior |
|---|---|---|
| `_pack_manager(ctx)` | 21 | Resolves workspace from `ctx.loop.workspace` (falls back to `ctx.loop.context.workspace`); returns `None` → handlers return an error string |
| `_next_index(pm, name)` | 32 | `max(meta["indices"]) + 1`, or 1 if empty/missing |
| `_format_pack_table(packs)` | 43 | Markdown table of `Name │ Sessions │ Updated │ Status` |
| `async cmd_pack(ctx) -> str` | 64 | Builds `channel:topic#NN` from current key's channel + next index; calls `pm.resolve()` |
| `async cmd_pack_list(ctx) -> str` | 101 | `pm.list_packs()` → table |
| `async cmd_pack_search(ctx) -> str` | 110 | `pm.search(query)` → ranked results |
| `async cmd_pack_summarize(ctx) -> str` | 135 | `pm.summarize()` with optional explicit text |

Handlers return `str` (not `OutboundMessage`) and are registered via
`router.exact/prefix` in `builtin.py` (see §2.6).

### 2.6 Integration points in `nanobot/command/builtin.py`

| Location | Lines | What |
|----------|-------|------|
| Import | 17 | `from nanobot.session.pack import parse_session_key` |
| `/status` pack section | 321–362 | `parse_session_key(ctx.key)` → `PackManager` → pack details appended to status |
| Command registration | 1143–1151 | lazy `from nanobot.command.pack_cmds import ...` + `router.exact/prefix` for all four commands |

`nanobot/session/__init__.py` (lines 4–8) re-exports
`SessionPackKey, format_session_key, has_pack_index, parse_session_key,
PackManager` — if upstream changes `session/__init__.py` exports, preserve these.

### 2.7 Merge risk

- **Low** for the new files (`pack.py`, `pack_manager.py`, `pack_cmds.py`) —
  they have no upstream counterpart.
- **Medium** for `builtin.py`: the import line, the `/status` block, and the
  registration block all sit inside heavily-trafficked upstream code. Upstream
  additions to `register_builtin_commands()` are the most common conflict.

### 2.8 Conflict-resolution playbook

1. **Never lose the new files.** `pack.py`, `pack_manager.py`, `pack_cmds.py`
   are self-contained; keep them verbatim.
2. **Registration:** upstream frequently appends new command registrations in
   `register_builtin_commands()`. The pack lines must be re-appended **after**
   the upstream registrations (they end with `router.prefix("/pack-summarize ", ...)`).
3. **Import:** keep `from nanobot.session.pack import parse_session_key` at the
   top of `builtin.py`; if upstream adds conflicting imports, merge them.
4. **`/status` block:** the `pack_section` block is wrapped in
   `with suppress(Exception):` so a resolution mismatch degrades to no-op rather
   than crashing `/status`. Keep that pattern.
5. Verify with §6 + merge guard:

```
After rebase, verify:
1. nanobot/session/pack.py and nanobot/session/pack_manager.py exist.
2. 'from nanobot.session.pack import parse_session_key' is in builtin.py.
3. All four /pack* registrations present in register_builtin_commands().
4. The /status pack_section block is present in cmd_status().
```

---

## Feature 3: CLI Cancel/Interrupt — Escape cancels turn, Ctrl+C force-exits

**Status:** Merged into `ys-nanobot/improvements` (commits `40e1eb48`,
`4e35a71e` "Fix chat interrupt keys", `ce741ce6` "Apply to refactored agent.py")

### 3.1 Motivation & design

During interactive `nanobot agent` mode, while the agent is generating or
running tools, the user needs keyboard control:

- **Escape** → cancel the current turn (equivalent to typing `/stop`), stay in
  the app.
- **Ctrl+C** → force-exit the whole app from any state.

The design uses a **background daemon thread** that puts the terminal into
**input-raw mode** and polls stdin with `select.poll()`.

> **Critical implementation detail (why not `tty.setraw()`):**
> `tty.setraw()` clears **OPOST** (output processing), disabling `\n` → `\r\n`
> translation — output "staggers" rightward while the monitor is active.
> This module only modifies **input** flags (clears `ICANON`, `ECHO`, `ISIG`,
> …) and **preserves output flags**, so rendering is unaffected. Do not
> "simplify" this back to `tty.setraw()`.

State coverage:

| State | Escape | Ctrl+C |
|---|---|---|
| Processing (turn active) | monitor → `/stop` on bus → turn cancels | monitor → shutdown events → clean exit |
| At prompt (idle) | no-op | existing SIGINT handler → "Goodbye!" → exit |

### 3.2 `watch_control_keys` contract (`nanobot/cli/input_monitor.py`, 160 lines)

```python
def watch_control_keys(
    *,
    on_escape: Callable[[], None] | None = None,
    on_ctrl_c: Callable[[], None] | None = None,
    stop_event: asyncio.Event,
    poll: float = 0.15,
) -> threading.Thread:   # daemon; returns None if stdin is not a TTY or stop_event already set
```

- Recognizes only `\x1b` (Escape) and `\x03` (Ctrl+C); all other bytes are
  discarded. Detection is byte-presence in each 64-byte read, so callbacks may
  fire once per read containing the byte.
- The thread **saves** `termios.tcgetattr(fd)`, applies input-raw config,
  loops `select.poll()` while `not stop_event.is_set()`, and restores attrs in
  a `finally` block (terminal safety on every exit path).
- **Callbacks run on the background thread.** Bridge to the async loop with
  `asyncio.run_coroutine_threadsafe(coro, loop)`.
- Module is stdlib-only (`asyncio`, `os`, `select`, `sys`, `threading`,
  `termios`, `tty` — `termios` imported lazily inside the function for
  Windows-safe module import).

### 3.3 Wiring in `nanobot/cli/agent.py` (`run_interactive()`)

| Location | Lines | What |
|----------|-------|------|
| Imports | 244–245 | `InboundMessage`; `from nanobot.cli.input_monitor import watch_control_keys` |
| Events | 279, 284–286 | `turn_done`, `shutdown_requested`, `monitor_stop` (asyncio.Event); `_interactive_loop = asyncio.get_running_loop()` |
| `_publish_stop_command()` | 288–295 | `bus.publish_inbound(InboundMessage(channel=cli_channel, sender_id="user", chat_id=cli_chat_id, content="/stop"))` |
| `_on_escape()` | 297–302 | `asyncio.run_coroutine_threadsafe(_publish_stop_command(), _interactive_loop)` + done-callback that surfaces exceptions |
| `_on_ctrl_c()` | 304–309 | sets `shutdown_requested`, `turn_done`, `agent_loop.stop()`, `monitor_stop` |
| Monitor start | 407–413 | `monitor_stop.clear()` → `_monitor_thread = watch_control_keys(on_escape=..., on_ctrl_c=..., stop_event=monitor_stop)` |
| Wait / stop | 415–420 | `await turn_done.wait()` → `monitor_stop.set()` → `_monitor_thread.join(timeout=2)` |
| Shutdown check | 422–423 | `if shutdown_requested.is_set(): break` |
| Teardown | 454–459 | `finally:` → `monitor_stop.set()` + `agent_loop.stop()` + task cleanup |

**Escape reuses the existing `/stop` path** — no new cancellation primitive. The
`/stop` InboundMessage triggers `loop._cancel_active_tasks(ctx.key)` exactly as
if the user had typed it.

### 3.4 Merge risk

- **Low** for `input_monitor.py` (new, self-contained, stdlib-only).
- **Medium** for `cli/agent.py`: upstream is actively refactoring the CLI
  (this feature was already re-applied once after the
  `commands.py` → `agent.py` split in upstream commit `e2563e2e`).

### 3.5 Conflict-resolution playbook

1. Keep `input_monitor.py` verbatim — it never conflicts with upstream.
2. In `run_interactive()` ensure the **three events** exist:
   `shutdown_requested`, `monitor_stop`, `turn_done`.
3. Wrap the `await turn_done.wait()` call with the monitor
   start/stop/join block (lines 407–420) — this is the block upstream churn
   most likely to displace. The monitor must start **after** the inbound
   message is published and **before** the wait; it must stop **after** the
   wait resolves.
4. Keep `_on_escape` / `_on_ctrl_c` definitions and the
   `monitor_stop.set()` in the `finally` block.
5. If upstream moves the channel/chat_id derivation, `_publish_stop_command`
   must keep using `cli_channel` / `cli_chat_id` (split from `session_id`
   at lines 255–258).

---

## Feature 4: Tool-Call Performance (per-tool disk latency)

**Status:** Merged into `ys-nanobot/improvements` (commit
`perf(tools): cut per-tool-call disk latency` — current HEAD of the branch)

### 4.1 Motivation & design

When the agent workspace (or the config dir holding sessions) sits on a slow
mount (e.g. a network/HDD drive), **every tool call** pays fixed disk costs
that stall the event loop for tens to hundreds of ms:

1. **Tool-result offload writes** — any result > `max_tool_result_chars`
   (default 16,000) is written to `{workspace}/.nanobot/tool-results/` via
   `_write_text_atomic()`, which does **file fsync + directory fsync**.
   Measured on a slow mount: **~46–54 ms per write** (vs 0.04 ms on `/tmp`).
2. **Runtime-checkpoint sidecars** — the runner emits **two checkpoints per
   tool batch** (`awaiting_tools` + `tools_completed`), each synchronously
   serializing the whole transcript state and writing it next to the session
   file **on the event loop** (grows quadratically with transcript size).

Three fixes, all behavior-preserving for normal (fast-disk) setups:

1. **No-fsync offload writes** — regenerable cache files skip fsync.
2. **Fast local dirs** — tool-results and runtime-checkpoint sidecars can be
   redirected to `~/.cache/nanobot/...` (config knob + automatic default).
3. **Coalesced, off-loop checkpoint writes** — checkpoints are captured
   synchronously but written via `asyncio.to_thread` with per-session
   coalescing (identical payloads skipped), so the event loop never blocks.

### 4.2 Exact code contracts

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
    results_dir: Path | None = None,   # NEW: overrides {workspace}/.nanobot/tool-results
) -> Any
```

**`nanobot/config/schema.py` — `AgentDefaults`:**

```python
tool_results_dir: str | None = Field(
    default=None,
    validation_alias=AliasChoices("toolResultsDir", "tool_results_dir"),
    serialization_alias="toolResultsDir",
)  # None = workspace default; relative paths resolve against the workspace
```

**`nanobot/agent/context_governance.py` / `nanobot/agent/runner.py`:**

- `ContextGovernanceConfig.tool_results_dir: Path | None = None` — passed to
  `maybe_persist_tool_result(results_dir=...)`.
- `AgentRunSpec.tool_results_dir: Path | None = None` — threaded into
  `ContextGovernanceConfig` in `AgentRunner.run()`.

**`nanobot/agent/loop.py`:**

```python
def _runtime_checkpoint_cache_dir() -> Path | None:
    # ~/.cache/nanobot/runtime-checkpoints (falls back to /tmp, then None)
    # passed as SessionManager(checkpoint_dir=...) in create_loop()

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
        self._checkpoint_dir = ...  # None → checkpoints next to session files
    def get_runtime_checkpoint_path(self, key):  # uses _checkpoint_dir if set
    def save_runtime_checkpoint_snapshot(self, session, *, payload, provider_state):
        # writes a captured snapshot without reading the mutated session
    def _write_checkpoint_unlocked(self, session, *, checkpoint, provider_state):
        # shared write helper (caller holds _session_files_lock)

class SessionManager:
    def __init__(self, workspace, *, store=None, sessions_root=None, checkpoint_dir=None):
    def save_runtime_checkpoint_snapshot(self, session, *, payload, provider_state):
        # facade: policy.persist check → _jsonl_store delegation → _remember()
```

### 4.3 Integration points (only places conflicts can occur)

| File | Location | What conflicts |
|------|----------|----------------|
| `nanobot/utils/helpers.py` | `_write_text_atomic`, `maybe_persist_tool_result` | Upstream changes to atomic-write or offload signatures |
| `nanobot/config/schema.py` | `AgentDefaults` | Upstream adds new default fields |
| `nanobot/agent/context_governance.py` | `ContextGovernanceConfig` | Upstream dataclass churn |
| `nanobot/agent/runner.py` | `AgentRunSpec` + `run()` | Upstream spec/runner refactors |
| `nanobot/agent/loop.py` | `_set_runtime_checkpoint`, `create_loop()` SessionManager construction | **Hot zone** — upstream checkpoint/recovery refactors |
| `nanobot/session/manager.py` | `JsonlSessionStore.__init__`, `SessionManager.__init__`, checkpoint writers | Upstream session-store churn |

### 4.4 Merge risk

- **Medium–High** for `loop.py` and `session/manager.py`: upstream is actively
  refactoring checkpoint/recovery plumbing (this feature was authored right
  after the "make run usage explicit" refactor that removed `loop._last_usage`).
- **Low** for the rest: the schema field, dataclass fields, and `fsync` kwarg
  are purely additive.

### 4.5 Conflict-resolution playbook

1. **`_write_text_atomic`**: if upstream rewrote it, re-add the
   `fsync: bool = True` keyword and the two `fsync=False` calls inside
   `maybe_persist_tool_result`. If `maybe_persist_tool_result` changed, keep
   the `results_dir` kwarg and
   `ensure_dir(results_dir if results_dir is not None else workspace / _TOOL_RESULTS_DIR)`.
2. **Dataclass plumbing**: keep `tool_results_dir` on `AgentRunSpec` **and**
   `ContextGovernanceConfig`, plus the two pass-throughs (`AgentRunSpec →
   ContextGovernanceConfig` in `run()`; `ContextGovernanceConfig →
   maybe_persist_tool_result` in `normalize_tool_result()`).
3. **`_schedule_checkpoint_write`** is the block upstream churn most likely to
   displace. If `_set_runtime_checkpoint` changed, re-add the coalescing writer
   verbatim (capture → token → `asyncio.to_thread` → snapshot write).
4. **Session store**: keep `checkpoint_dir` on both `JsonlSessionStore` and
   `SessionManager.__init__`, and the `_checkpoint_dir` branch in
   `get_runtime_checkpoint_path()`. Keep `save_runtime_checkpoint_snapshot` on
   both classes (`_write_checkpoint_unlocked` is the shared writer).
5. Verify with the checklist in §6 (commands) and the merge guard:

```
Search for _schedule_checkpoint_write in nanobot/agent/loop.py and
save_runtime_checkpoint_snapshot in nanobot/session/manager.py.
If missing → re-add both (the coalescing writer + the snapshot facade).
Also confirm _runtime_checkpoint_cache_dir() is used in create_loop().
```

---

## 5. Step-by-Step Merge Procedure (from `main`)

Run this exact sequence every time. It is designed so that even a chaotic
upstream merge cannot silently drop a feature.

```bash
# 1. Snapshot & fetch
git status --porcelain          # must be clean; stash if not
git tag "safety/pre-merge-$(date +%Y%m%d-%H%M%S)" HEAD
git fetch origin main           # upstream HKUDS/nanobot

# 2. Pre-merge state check (both directions!)
git rev-list --left-right --count HEAD...origin/main   # expect "N 0" (0 = no new upstream)
git log --oneline HEAD..fork/ys-nanobot/improvements   # expect EMPTY after ff (features present)
git merge-base --is-ancestor origin/main HEAD && echo "main fully merged" || echo "MUST MERGE main"

# 3. If upstream has new commits, merge it
git merge origin/main           # resolve conflicts using the per-feature playbooks above

# 4. Re-apply features if anything was lost (see §6 checklist); then ensure
#    the feature commits are still reachable
git log --oneline HEAD..fork/ys-nanobot/improvements   # should be empty → features intact
```

If the fork branch is ahead of HEAD (features missing locally), fast-forward:

```bash
git merge --ff-only fork/ys-nanobot/improvements
```

**Never** force-push `ys-nanobot/improvements` unless you own the remote copy
(`git push --force-with-lease`, never `--force`). Do **not** rewrite the local
`main` / `origin/main`.

## 6. Quick Conflict Checklist

After any merge/rebase, confirm every row:

| What to check | Where | Why |
|---------------|-------|-----|
| `_load_channel_prompt` method + call site | `nanobot/agent/context.py` | Channel-specific prompts |
| Channel line in `/model` | `nanobot/command/builtin.py` `_model_command_status()` | Channel display |
| `parse_session_key` import | `nanobot/command/builtin.py` (line 17) | Session pack parsing |
| Pack section in `/status` | `nanobot/command/builtin.py` `cmd_status()` (321–362) | Pack display |
| Four `/pack*` registrations | `nanobot/command/builtin.py` `register_builtin_commands()` (1143–1151) | Pack commands |
| `input_monitor.py` | `nanobot/cli/input_monitor.py` | Cancel/interrupt |
| `watch_control_keys` usage + 3 events | `nanobot/cli/agent.py` `run_interactive()` | Cancel/interrupt |
| `session/pack.py` | `nanobot/session/pack.py` | Pack key parsing |
| `session/pack_manager.py` | `nanobot/session/pack_manager.py` | Pack CRUD |
| `command/pack_cmds.py` | `nanobot/command/pack_cmds.py` | Pack commands |
| `session/__init__.py` exports | `nanobot/session/__init__.py` (lines 4–8) | Public API surface |
| `_write_text_atomic` `fsync` kwarg + `results_dir` | `nanobot/utils/helpers.py` | Perf: no-fsync offload |
| `toolResultsDir` config | `nanobot/config/schema.py` `AgentDefaults` | Perf: fast local offload dir |
| `_schedule_checkpoint_write` + `_runtime_checkpoint_cache_dir` | `nanobot/agent/loop.py` | Perf: coalesced off-loop checkpoints |
| `save_runtime_checkpoint_snapshot` + `checkpoint_dir` | `nanobot/session/manager.py` | Perf: snapshot writer + fast checkpoint dir |

## 7. Post-Merge Verification (commands)

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
]
for f in files:
    ast.parse(open(f).read())
    print(f"OK  {f}")
EOF

# Imports resolve (catches broken relative imports / renamed symbols)
uv run --no-sync python -c \
  "from nanobot.session.pack import SessionPackKey, parse_session_key; \
   from nanobot.session.pack_manager import PackManager; \
   from nanobot.cli.input_monitor import watch_control_keys; \
   from nanobot.agent.context import ContextBuilder; \
   from nanobot.session.manager import SessionManager; \
   from nanobot.utils.helpers import _write_text_atomic, maybe_persist_tool_result; print('imports OK')"

# Full gate (matches CI): lint + strict type check
ruff check nanobot/
uv run --no-sync basedpyright

# Functional smoke (manual): nanobot agent → during a long turn press
# Escape (turn cancels, app stays) and Ctrl+C (clean "Goodbye!" exit).
# Perf sanity: a large tool result (>16k chars) should not stall the loop;
# check that {workspace}/.nanobot/tool-results or the configured
# toolResultsDir receives the offload file with no fsync delay.
```

---

*Maintained as part of the `nanobot-improvements` task
(`user-tasks/nanobot-improvements/USER-TASK.md`). Update this document whenever
a feature's code contracts change — an outdated merge reference is worse than
none.*
