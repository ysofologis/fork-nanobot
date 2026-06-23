# Hard Merge Guide: Merging `main` While Excluding a Feature

## Context

This guide documents the merge of `main` into `merge/agent-colab` while **preventing the `message-bus` feature changes** from being brought in. The message-bus feature (multi-backend NATS/Redis/ZMQ bus) existed on BOTH branches, but in different states:

| Branch | Bus State |
|--------|-----------|
| `main` | Simplified bus — in-process only, backends removed |
| `merge/agent-colab` (ours) | Full bus — NATS, Redis, ZMQ, config, factory preserved + agent collaboration additions |

**Goal**: Merge `main`'s bugfixes, docs, and webui improvements into our branch **without** letting main's simplified bus overwrite our full bus.

---

## Phase 1: Pre-Merge Analysis

### 1.1 Understand the diff direction

```bash
# What changed FROM our branch TO main
git diff --stat our-branch..main -- path/to/feature/

# Which commits touched the feature on each branch
git log --oneline our-branch -- path/to/feature/
git log --oneline main -- path/to/feature/

# Find the merge base
git merge-base HEAD main
git show $(git merge-base HEAD main):path/to/feature/file
```

**Critical insight**: A diff can be misleading. `git diff A..B` shows what changed FROM A TO B. If main DELETED files compared to us, the diff shows deletions — but that means WE have the full version, not main.

### 1.2 Check what non-feature files also differ

```bash
# All changed files excluding the feature directory
git diff --stat our-branch..main -- . ':!path/to/feature/'
```

This tells you what ELSE you'll be merging in besides the feature you want to block.

### 1.3 Identify files that reference the feature outside its directory

Files like `nanobot/cli/commands.py` may import from the bus (`from nanobot.bus.factory import create_bus`). These will need careful conflict resolution even if the bus files themselves are fine.

---

## Phase 2: Executing the Merge

### 2.1 Start with `--no-commit`

```bash
git merge main --no-commit --no-ff
```

`--no-commit` prevents git from auto-creating the merge commit, giving you a chance to inspect and fix conflicts first. `--no-ff` ensures a merge commit even if a fast-forward is possible.

### 2.2 Check which files were auto-merged vs conflicted

```bash
# Conflicted files
git diff --name-only --diff-filter=U

# All modified files
git status --short
```

Files with `UU` are unmerged. Files with `M` were auto-merged — verify they're correct.

### 2.3 Verify the feature files survived intact

```bash
# Check if any feature files were modified by the merge
git diff --cached -- path/to/feature/

# If they show as unmodified, the merge preserved your version
```

**Why auto-merge works for excluded features**: If only YOUR branch modified the feature files relative to the merge base, git's three-way merge keeps your version automatically. If both branches modified them, you'll get conflicts.

---

## Phase 3: Conflict Resolution Patterns

### Pattern A: Divergent function structure (loop.py)

**Symptom**: One branch has extra logic (agent collaboration) that the other doesn't.

**Resolution**: Keep your branch's version when the feature depends on it. In our case, `loop.py` had:
- Agent-to-agent messaging via `bus.agent_inbound`
- Target routing and reply/forward logic
- `CancelledError` and general exception handling
- `|bot-name>` routing syntax

→ **Keep HEAD (ours)** for all conflict regions.

### Pattern B: Restructured CLI commands (commands.py)

**Symptom**: One branch has `@app.command() def gateway(...)` wrapper, the other has `def _run_gateway(...)` directly.

**Resolution**: Keep your branch's command structure (`@app.command() def gateway(...)`) since it provides the CLI entry point. For shared logic (shutdown), **combine both**:
- Keep our `bus.stop()` and `agent.close_mcp()`
- Add main's task management (`shutdown_task.cancel()`, `runtime_tasks`)
- Add main's `restore_shutdown_handlers()`

### Pattern C: Combined imports (test_commands.py)

**Symptom**: Each branch imports different symbols.

**Resolution**: Include imports from BOTH branches.

---

## Phase 4: Post-Merge Validation

This is where things can go wrong even with no syntax errors.

### 4.1 Check for STRUCTURAL bugs (critical)

After resolving merge conflicts, **manually trace the control flow** of the most complex functions. We found these bugs post-merge:

#### Bug Type 1: Orphaned `try`/`finally`
```
  try:
      await do_something()
      logger.info("done")
  
  while condition:        # ← This is OUTSIDE the try!
      ...
  finally:                # ← Orphaned finally!
      await cleanup()
```

**Fix**: Either remove both the `try` and `finally` (if the original didn't have them), or wrap the entire block.

#### Bug Type 2: Misindented dispatch logic
```
  if session_in_pending_queue:
      ...
      continue    # ← routes to pending queue
  
  # These lines got indented INTO the if during merge!
  task = create_task(dispatch(msg))   # ← NEVER REACHED for new sessions!
```

**Fix**: Ensure fallthrough logic (the "normal" case) is at the correct indentation level — **outside** the condition that handles the special case.

### 4.2 Verify Python syntax

```bash
python3 -c "import py_compile; py_compile.compile('path/to/file.py', doraise=True)"
```

Check ALL files that were modified, not just conflicted ones.

### 4.3 Runtime test

Run the application and try the affected feature path:
```bash
nanobot agent -m "hello" --logs
```

Watch for:
- Immediate crashes (SyntaxError/ImportError)
- Hangs (infinite loop, deadlock, task never dispatched)
- Wrong behavior (wrong version of feature running)

---

## Phase 5: Commit & Documentation

### 5.1 Amend vs fresh commit

If the merge is still the most recent uncommitted operation:
```bash
git add <resolved-files>
git commit --amend --no-edit
```

If you've already committed but need to fix:
```bash
git add <fixed-files>
git commit --amend --no-edit
```

### 5.2 Document the merge for future reference

Save a guide like this one. Include:
- Which feature was blocked and why
- Which files had conflicts
- The resolution strategy for each conflict
- Any post-merge bugs found and their fixes

---

## Quick Checklist

```
Pre-merge:
[ ] git diff --stat our..main -- path/to/feature/   (understand diff direction)
[ ] git log --oneline our..main                      (valuable changes to merge)
[ ] git merge-base HEAD main                          (find common ancestor)

Merge:
[ ] git merge main --no-commit --no-ff
[ ] git diff --name-only --diff-filter=U              (conflicts)
[ ] git diff --cached -- path/to/feature/             (feature files preserved)
[ ] Resolve conflicts
[ ] git add <resolved files>

Post-merge:
[ ] python3 -c "import py_compile; py_compile.compile(...)"  (all changed files)
[ ] Trace control flow in complex functions (no orphaned blocks, correct indentation)
[ ] Run the application and test the affected paths
[ ] git commit
```
