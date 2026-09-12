# Harness speed & tool-economy improvements

## Goal
Cut wall-clock per turn and stop mid-task halts for the finance/bookkeeping flow.
Targets: (1) stop holding the reply hostage to memory writes, (2) cut needless tool
round-trips (blind filesystem scans, wrong DB paths, futile HITL, iterative compile
fixes), (3) never lose a generated chart, (4) raise/soften the hard request cap, and
(5) stream the reply so long turns feel short. Do not change the configured model.

## Evidence
Two environments measured. Local (`.lattice/logs/lattice.log`, 09-11→09-12) uses
`deepseek/deepseek-v4.1-flash` and is request-latency-bound (~25–35s/request).
VPS (`~/Workspaces/lattice`, 09-12) uses `deepseek/deepseek-v4-flash-0731`, where most
requests are 1–5s but turns are still 34–394s.

Highest-impact finding (VPS): **`memory.sync_turn` is awaited on the reply path.**
`run_turn`'s `finally` block calls `await memory.sync_turn(...)` *before* returning
`Outbound`, so Telegram's send waits for a fastembed/Qdrant write:
- `turn=79301fc3a18b` 253.9s total, `memory_sync_ms=73402` (73s)
- `turn=1d4034ee7679` 152.9s, `memory_sync_ms=55074`
- `turn=6efeb77d9932` 165.7s, `memory_sync_ms=37583`
- `turn=9a7cf8f61110` 128.3s, `memory_sync_ms=29426`; `turn=cc1178acfc26` 187.9s, `8682`

Other measured waste:
- **Budget abort:** `turn=6635170a2c69` = 393.6s, 60 tool calls, hit
  `request_limit=40` and returned the canned "step budget" message with the skill
  half-built. The follow-up turn then built a *second* skill (`finance`) and the user
  had to delete the stale `personal-finance` one.
- **Media not attached:** the agent generated `sept-line.png` by editing the script and
  running it via `shell`; only `generate_chart`/`generate_pdf` append to
  `deps.outbound_media`, so nothing was sent. The user then said "You haven't sent the
  line chart" and the agent spent a whole extra turn (`turn=1d4034ee7679`, 152.9s,
  24 tool calls) to regenerate it.
- **Wrong DB path:** `sqlite_register finance path=finance.db` resolved to
  `.lattice/sqlite/finance.db` (empty new file), not `.lattice/workspace/finance.db`.
  `sqlite_schema` → `(empty schema)`, `sqlite_query` → `no such table: meta`, then the
  agent burned several `execute_script`/`shell` calls diagnosing it.
- **Futile HITL:** `remove_path` calls `maybe_approve` before the path-jail check, so two
  approvals were requested for absolute paths that then failed as "outside
  workspace/skills/profiles/scripts/tools".
- **Blind exploration every turn:** `find / -name sept-line.png`, `find / -name
  sept-expenses.png`, repeated `ls`/`grep` to locate `finance.py` and `finance.db`. `find
  /` also hit the 60s shell timeout twice in the local log.
- **Stray ledgers:** the skill script's DB default is cwd-relative; after the model
  `cd`'d to the repo root and to `.lattice`, it created empty `./finance.db` and
  `.lattice/finance.db` that then had to be removed.
- **Compile-fix loop:** a 29KB `finance.py` was written in one shot, then fixed with
  ~10 `edit_file`/`python3 -m py_compile`/`shell` round-trips.
- **No token streaming:** `LiveTurnEvents.on_stream_delta` is a no-op; the user watches a
  spinner for the full turn.
- **Stale runtime skills (local):** non-clobbering seeding left the old
  `skill-authoring` and an old `sqlite-admin/scripts/sqlite.py` that read
  `databases.json` while the runtime writes `databases.yaml` → `(no registered
  databases)`.

## Decisions
- Keep the configured model. Fix harness overhead, not the provider.
- **Memory writes become off-path**: a single serialized background worker (one queue)
  consumes `sync_turn` jobs; `run_turn` no longer awaits it before replying. Flush the
  queue on gateway shutdown. Trade-off: a crash between reply and flush can lose the
  last turn's memory — acceptable for a personal assistant; the alternative (bounded
  await, e.g. 2s, then background) is the fallback if durability is required.
- Environment context goes in the volatile tail (`PromptBundle.notices`), not the cached
  system prefix.
- Cold tool tiering is **not** a bottleneck here: the VPS logs call `generate_chart`,
  `execute_script`, `sqlite_*`, and `skill_view` directly with no `search_tools` round
  trip. Drop the earlier "promote tools to eager" task.
- Media is attached by detecting files created during the turn under the workspace, in
  addition to the existing `generate_chart`/`generate_pdf` path.
- Bundled skill refresh is hash-based so operator edits survive.

---

## Tasks (ordered by measured impact)

### T1. Move memory writes off the reply critical path  ← biggest win
Files: `src/lattice/turn.py`, `src/lattice/memory/base.py` (docs), new
`src/lattice/memory/worker.py` (or a small helper in `turn.py`).
- Add a module-level serialized memory writer: an `asyncio.Queue` + one worker task per
  process. `run_turn` calls `enqueue_sync(memory, messages[-4:])` and returns.
- Keep `memory_prefetch` on-path (only 130–770ms) but the compression-path
  `memory.sync_turn(messages)` also becomes a background enqueue.
- Flush on shutdown: gateway/CLI drains the queue (bounded, e.g. 5s) before exit; also
  drain in `close_memory()`/atexit best-effort.
- Log one line per job with duration so `memory_sync_ms` stays measurable without
  blocking.
- Tests: unit test that a slow `sync_turn` does not delay the returned `Outbound`; queue
  ordering; flush drains.

### T2. Auto-attach media created during the turn
Files: `src/lattice/turn.py`, `src/lattice/tools/agent/execute_script.py` (optional
declaration), `src/lattice/channel/*` (no change if `outbound_media` used).
- Snapshot existing media under `workspace` before the run; after, glob
  `*.png|jpg|jpeg|webp|gif|pdf|svg` with `mtime >= turn_start`, excluding
  `workspace/inbound/`, and attach (dedup with existing `deps.outbound_media`, cap count
  and size).
- Keeps `generate_chart`/`generate_pdf` behavior; catches script/shell-produced charts.
- Tests: a turn that writes a PNG via `execute_script` yields it in `Outbound.media_paths`;
  an uploaded file in `inbound/` is not re-attached.

### T3. Validate paths before HITL
Files: `src/lattice/tools/agent/remove_path.py`, `src/lattice/tools/files.py`
(expose a `resolve_removable`/dry-run), `src/lattice/tools/agent/profile_remove.py`.
- Resolve and jail-check the target first; if outside the jail (or otherwise invalid),
  return the error immediately without prompting. Only call `maybe_approve` when the
  operation is actually executable.
- Apply the same validate-before-approve rule to `profile_remove` (invalid id should
  error, not prompt).
- Tests: out-of-jail `remove_path` returns an error and never calls the HITL port;
  in-jail removal still prompts when policy requires.

### T4. Fix relative SQLite registration
Files: `src/lattice/sqlite/registry.py` (`_resolve_db_path`), `tests/test_memory_sqlite_tools.py`.
- For a relative `path`, resolve against the workspace first if that file exists;
  otherwise fall back to `home/sqlite/<name>.db`. Pass the workspace into the registry
  (or the register call) so it can decide.
- Return the resolved absolute path in the tool result and a clear message when it
  creates a new file, so the model can self-correct (the VPS confusion came from a
  silent wrong-path registration).
- Tests: relative path matching a workspace file registers that file; a non-existent
  relative path still lands under `home/sqlite`.

### T5. Inject compact runtime context each turn
Files: `src/lattice/prompt.py`, `src/lattice/turn.py`.
- Append to `notices` (volatile tail): workspace absolute path; current local time +
  timezone; registered DBs (`name → path`) and, for the active profile, the canonical
  ledger/db paths; available user tools; profile id and preferred skills; and short
  rules: "shell cwd is already the workspace — do not `cd`; do not `find /` or scan
  `~`; use `search_files`/`read_file`/`sqlite_schema`; the canonical finance DB is
  `<path>`."
- This directly kills the repeated `find /`, `ls`, `grep`, and stray-`cd` behavior.
- Keep under ~15 lines / ~1k chars.
- Tests: builder unit test asserting workspace/time/DB lines present.

### T6. Auto syntax-check scripts on write
Files: `src/lattice/tools/agent/write_file.py`, `src/lattice/tools/file.py` (or a helper).
- After `write_file`/`edit_file` of `.py`/`.js`/`.sh` (and YAML for `tools/*.yaml`),
  run a cheap validator (`py_compile`, `node --check`, `bash -n`, `yaml.safe_load`) and
  append `compile: ok` or the error to the tool result.
- Saves the `python3 -m py_compile` round-trips seen in the 29KB-script debugging loop
  and surfaces the `unmatched ')'` immediately.
- Keep it best-effort and non-fatal; skip large files over a cap.
- Tests: syntax error is reported inline; valid file reports ok; non-script files
  unaffected.

### T7. Shell guard + tighter default timeout
Files: `src/lattice/tools/shell.py`, `src/lattice/tools/agent/shell.py`,
`src/lattice/hitl/policies.py` (shared helper), tests.
- Preflight reject root-wide scans with no narrow root (`find /`, `find ~`,
  `find $HOME`, `du /`, `grep -r /`, `ls -R /`) with a message pointing at
  `search_files` / `sqlite_schema`.
- Lower default `timeout` 60 → 30s; clamp to a max (~120s).
- Tests: guarded command rejected without executing; normal workspace commands run.

### T8. Budget: stop hard-halting normal tasks
Files: `src/lattice/config.py` (`AgentConfig.iteration_budget`), `lattice.yaml`,
`default_config_yaml()`, `src/lattice/turn.py`.
- Raise default 40 → 60 (configurable); reword the abort to "model requests" and keep
  the continue hint.
- Fewer round trips (T1–T6) plus the higher cap means the 393s abort that left a
  half-built skill should not recur.
- Stretch: drive `agent.iter()`, watch `agent_run.usage.requests`, enqueue a "wrap up
  now" instruction before the cap; or resume from
  `agent_run.ctx.state.message_history` with a fresh limit. `UsageLimitExceeded`
  carries no messages in pydantic-ai 2.42, so this needs `agent.iter()`.

### T9. Trim tool-result sizes
Files: `src/lattice/deps.py` (`truncate_result` 30_000 → ~12_000),
`src/lattice/tools/shell.py` (stdout 50_000 → 20_000),
`src/lattice/tools/file.py` (head large files with a line count and a "use edit/search"
hint).
- The 11KB `finance.py` was read into context more than once; capping cuts request
  tokens and compression.
- Update tests asserting old limits.

### T10. Stream assistant tokens to the channel
Files: `src/lattice/turn.py`, `src/lattice/channel/live_status.py`,
`src/lattice/channel/telegram/bot.py`, `src/lattice/channel/cli/adapter.py`.
- Pass `event_stream_handler=` to `agent.run(...)` (pydantic-ai 2.42); on text
  `PartDeltaEvent`s call `await events.on_stream_delta(delta)`.
- Implement `LiveTurnEvents.on_stream_delta`: accumulate partial text and render it
  under the elapsed header with the existing throttle; Telegram edits the status bubble.
  Final answer still sent as a new message; bubble deleted after.
- Keep error/retry/compress handling; a late failure must still produce a final message.
- Tests: `tests/test_live_status.py` accumulation/throttle/cap; turn-level delta test if
  practical.

### T11. Refresh bundled skill assets on existing homes
Files: `src/lattice/setup.py` (`write_skill_starters`, `seed_skill_scripts`, `init_home`).
- Manifest `<home>/skills/.bundled.json` with `{rel_path: sha256}`; refresh only when the
  on-disk hash matches the last shipped hash, back up replaced files, skip/report
  operator-edited ones. Run at init and gateway/CLI boot.
- Fixes stale `skill-authoring`/`sqlite-admin` on long-lived homes.
- Tests: unmodified refreshed, edited preserved.

### T12. Diagnostics (investigate before/while implementing)
- `execute_script` took **116s** in `turn=6635170a2c69` (17:08:11→17:10:07) for an inline
  smoke test. Reproduce and profile; check whether soft-sandbox script work or a hung
  subprocess is responsible.
- A gateway restart line appeared mid-turn at 17:06:59 in `turn=6635170a2c69`; confirm
  whether the 40-request/60-tool-call turn crashed or was redeployed.
- `lattice.llm` per-call logging is present on the VPS (good); confirm it is enabled in
  the local run too.
- Minor cleanup: dead duplicate `return` in
  `src/lattice/memory/mem0_qdrant.py` `build_memory` (lines ~456–457).

---

## Validation
- `uv run ruff check src tests` and `uv run ruff format src tests`
- `uv run pytest` (update tier/seed/status/media/registry tests)
- Manual smoke on `finance`:
  1. "yesterday I bought batagor for 10K and petrol for 80K" → reply returns without a
     multi-second post-answer stall (`memory_sync_ms` no longer gates `send`).
  2. "give me this month expenses chart" → chart is attached even when produced by the
     script/shell path; no "you didn't send it" follow-up.
  3. Re-run the "create the skill" prompt → should not hit the request cap mid-build and
     should not create a duplicate skill or stray `finance.db` files.
- Log asserts: no `find /`, no futile `remove_path` approval, no `(empty schema)` after
  `sqlite_register`, `requests`/`tool_calls` drop, `END` closer to `executor_ms`.

## Out of scope
- Changing the model/provider or adding a fast secondary model.
- Auto-generating a `finance(action=…)` dispatcher tool (skill guidance already steers
  to one tool per domain).
- Full cancel/resume middleware beyond the T8 stretch.

## Risks
- Off-path memory sync can lose the last turn on an abrupt crash; flush-on-shutdown
  mitigates, and a bounded await is the fallback if stricter durability is needed.
- Media auto-attach must not re-send inbound uploads or unrelated old files: scope by
  turn start time + workspace root + extension, exclude `inbound/`, dedup, and cap.
- Compile-on-write runs a subprocess per code write; bound size/time and never fail the
  write because validation did.
- Background memory worker and the existing `_shared_qdrant_client`/mem0 instance must
  stay single-writer; reuse one worker and the cached memory instance to avoid Qdrant
  local-mode lock errors.
