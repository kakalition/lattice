# Plan: Standardize built-in tools into MCP-shaped, grouped namespaces

## Goal

Replace the 37 flat `src/lattice/tools/agent/<tool>.py` bindings with **11 group modules**
that mirror MCP's `server/tool` model, and expose every built-in tool under a namespaced
identity. Groups drive naming, policy, tiering, and discovery; external MCP servers and
user tools flow through the same naming scheme.

## Decisions (locked)

| Decision | Choice |
|---|---|
| Standardization scope | Group built-ins with an MCP-shaped interface; **no** wire protocol / subprocess MCP server |
| Namespace shape | `group/tool` |
| Leaf names | Trimmed MCP-style (`sqlite/query`, `memory/add`, `skills/view`) |
| Groups | 11 (README's 9 rows with `web`+`browser` and `skills`+`profiles` split) |
| Tool code layout | 11 group modules under `tools/groups/`, replacing the flat `tools/agent/` package |
| Interface | `ToolGroup` registry + thin `NamespacedToolset` wrapper; JSON schemas stay auto-generated from typed function signatures |
| Tiering | Per-tool defaults preserved; `tools.eager`/`tools.cold` globs match canonical names; mixed groups split into eager + deferred namespaced toolsets |
| Non-core names | User tools → `user/<name>`; external MCP → `server/tool`; built-in group names reserved |
| Legacy names | Compat alias layer (flat names and `prefix_*` globs normalized to canon on load) |
| Provider constraint | pydantic-ai sends `ToolDefinition.name` to the wire verbatim (`pydantic_ai/models/openai.py:1722`); OpenAI function names must match `^[a-zA-Z0-9_-]{1,64}$`. Canonical identity is `group/tool`; **wire name is `group__tool`** (slash → `__`). |

### Two name forms — the central invariant

- **Canonical** (`group/leaf`): operator config (`tools.allow/deny/eager/cold`, profile
  policy), HITL gates, audit, stats, action ledger, `CORE_TOOL_NAMES`, tests, README config
  docs.
- **Wire** (`group__leaf`): what the model sees and calls — tool schemas, `search_tools`
  results and manifest, prompt/notice text, `SYSTEM_SOUL`, skill bodies.

Rules:
- `wire_name(c) = c.replace("/", "__")`; `canonical_name(w) = w.replace("__", "/", 1)`.
- Name grammar forbids `__` inside any group or leaf segment, so the mapping is bijective.
- Enforce `^[a-z][a-z0-9]*$` for group names and `^[a-z][a-z0-9_]*$` (no `__`) for leaves;
  aggregate wire names must satisfy the provider regex and be ≤64 chars (max here is
  `schedule__timezone_set` = 22).

## Group / tool mapping

| Group | Canonical tools (tier) | Replaces |
|---|---|---|
| `files` | `shell`(E), `read`(E), `write`(E), `edit`(E), `remove`(E), `search`(E) | shell, read_file, write_file, edit_file, remove_path, search_files |
| `media` | `ocr`(E), `pdf`(C), `chart`(C) | ocr, generate_pdf, generate_chart |
| `web` | `search`(E), `fetch`(E) | web_search, web_fetch |
| `browser` | `interact`(C), `snapshot`(C) | browser_interact, browser_snapshot |
| `compute` | `script`(C), `calculator`(E) | execute_script, calculator |
| `interaction` | `clarify`(E), `todo`(E) | clarify, todo |
| `schedule` | `add`(E), `list`(C), `cancel`(C), `timezone_get`(C), `timezone_set`(C) | schedule_add/list/cancel, timezone_get/set |
| `memory` | `session_search`(E), `search`(E), `add`(E), `update`(C), `forget`(C) | session_search, memory_search/add/update/forget |
| `sqlite` | `list`(C), `schema`(E), `query`(E), `execute`(C), `register`(C), `unregister`(C), `backup`(C) | sqlite_list/schema/query/execute/register/unregister/backup |
| `skills` | `list`(E), `view`(E) | skills_list, skill_view |
| `profiles` | `list`(C), `remove`(C) | profile_list, profile_remove |

Eager count stays **20** (asserted by `tests/test_harness_efficiency.py:212`).

## New modules

1. `src/lattice/tool_names.py` — pure, dependency-free name registry (safe for `deps.py`
   to import without cycles):
   - `GROUP_NAMES`, `DEFAULT_TIERS: dict[str, ToolTier]`, `CORE_TOOL_NAMES: list[str]`.
   - `canonical_name`, `wire_name`, `leaf_of`, `group_of`.
   - `LEGACY_ALIASES: dict[str, str]` (every flat name → canonical) and
     `LEGACY_GLOB_ALIASES` (`sqlite_*`→`sqlite/*`, `web_*`→`web/*`, `memory_*`→`memory/*`,
     `schedule_*`→`schedule/*`, `profile_*`→`profiles/*`, `skill*`→`skills/*`,
     `browser_*`→`browser/*`, `generate_*`→`media/*`, `session_*`→`memory/*`).
   - `normalize_name(name)` / `normalize_pattern(glob)` (exact alias → canonical glob → identity).
   - `RESERVED_GROUPS` (11 group names + `user`, `mcp`) and `RESERVED_LEAVES`
     (all core leaves + `search_tools`).

2. `src/lattice/tools/groups/_common.py`:
   - `ToolBinding` (leaf, tier, register fn) and `ToolGroup` (name, description, bindings,
     `canonical_names()`).
   - `NamespacedToolset(WrapperToolset[TurnDeps])` mirroring pydantic-ai's
     `PrefixedToolset` (`toolsets/prefixed.py`) but joining with `__`; renders wire names
     in `get_tools`, strips the `"{group}__"` prefix in `call_tool` and resets
     `ctx.tool_name` to the canonical name.
   - `ToolsetT` alias (moved from `tools/agent/_common.py`).

3. `src/lattice/tools/groups/<group>.py` × 11 — each holds the moved pydantic-ai tool
   functions with trimmed local names, preserves each function's current body/docstring,
   declares tiers, and exports `GROUP = ToolGroup(...)`. Bodies update their explicit
   `traced(ctx, ...)` / `maybe_approve(ctx, ...)` first argument to the canonical name
   (e.g. `"files/shell"`, `"sqlite/execute"`).

4. `src/lattice/tools/groups/__init__.py` — runtime registry + builders:
   - `GROUPS: tuple[ToolGroup, ...]` in fixed order (files, media, web, browser, compute,
     interaction, schedule, memory, sqlite, skills, profiles).
   - `resolve_tier(name, *, eager, cold, default=None)` (same semantics; globs normalized
     via `normalize_pattern`; falls back to `DEFAULT_TIERS`).
   - `default_eager_names()`, `tool_functions(*, exclude)`, `_check_complete`.
   - `build_toolsets(...)`: per group, partition bindings by resolved tier into one eager
     and one deferred `FunctionToolset`, wrap each in `NamespacedToolset`, return
     `[all eager..., deferred...]` preserving the "revealed tool is a pure suffix"
     ordering invariant.
   - Re-export `CORE_TOOL_NAMES` from `lattice.tool_names`.

5. Delete `src/lattice/tools/agent/` entirely.

## Ordered task list

1. **Name registry**: add `src/lattice/tool_names.py`; point `deps.CORE_TOOL_NAMES` at it
   (removing the hardcoded list).
2. **Group scaffold**: add `tools/groups/_common.py` (`ToolGroup`, `ToolBinding`,
   `NamespacedToolset`, `ToolsetT`).
3. **Move tools**: create the 11 group modules, moving each existing
   `tools/agent/<tool>.py` body into its group with the trimmed leaf name and canonical
   `traced`/`maybe_approve` argument. Preserve behavior, docstrings, HITL calls, argument
   validation, and tier defaults. Delete `tools/agent/`.
4. **Builders**: implement `tools/groups/__init__.py` (`GROUPS`, `resolve_tier`,
   `default_eager_names`, `tool_functions`, `build_toolsets`).
5. **Agent wiring**: update `agent_app.py` imports; make `filter_enabled._keep`
   canonicalize the incoming `tool_def.name` before checking `deps.enabled_tools`; update
   `_toolset_cache_key`/`_user_tool_tier`; make `build_search_description` list **wire**
   names (`sqlite__execute`) while discovering over canonical `enabled`.
6. **Namespaced tool loops**:
   - `tools/user_tools.py`: expose `user/<name>` → wire `user__name`; `call_tool` strips
     namespace; HITL/trace use `user/<name>`; reserved check now rejects a leaf in
     `RESERVED_LEAVES`.
   - `mcp/toolset.py`: keep `mcp_tool_name` canonical (`server/tool`) but render wire names
     in `get_tools`; translate back in `call_tool`.
   - `mcp/hosts.py`: validate server/tool names against the grammar and reject reserved
     group names at registration; surface collisions as `notices`/`doctor` failures.
7. **HITL**: `hitl/policies.py` — `DESTRUCTIVE_GATE_TOOLS` becomes
   `{files/shell, sqlite/execute, sqlite/unregister, profiles/remove, compute/script,
   files/remove}`; `tool_needs_approval` normalizes the incoming name via
   `normalize_name` then switches on canonical; keep all arg-based logic verbatim.
8. **Search/ledger/trace**:
   - `tool_search.py`: canonicalize corpus names before alias lookup/tokenization; rekey
     `_ALIASES` to canonical (`media/chart`, `media/pdf`, `browser/*`, `compute/script`,
     `schedule/*`, `sqlite/*`, `memory/update|forget`, `profiles/*`). Wire names still
     returned to the framework.
   - `action_ledger.py`: canonicalize `part.tool_name` in `actions_from_messages`; update
     `_ARTIFACT_TOOLS` → `{files/write, files/edit, media/chart, media/pdf}` and
     `_EVIDENCE_TOOLS` → `{files/read, sqlite/query, sqlite/schema, web/fetch}`.
   - `turn.py`: `_MEDIA_TOOLS` → canonical (`files/shell`, `compute/script`,
     `files/write`, `files/edit`, `media/chart`, `media/pdf`, `browser/interact`,
     `browser/snapshot`, `files/remove`).
   - `turn_trace.py`: `on_tool_start` skill detection `name == "skills/view"`.
9. **Prompt / persona / skills** (model-facing → **wire** names):
   - `prompt.py`: `build_runtime_context` rules and `build_skill_index_xml` use wire names
     (`files__search`, `files__read`, `sqlite__schema`, `skills__view`).
   - `assets/SOUL.md` and the `profiles/load.py` `SYSTEM_SOUL` fallback: `interaction/clarify`,
     `interaction/todo`, `schedule/add`, `memory/add`, `skills/list`, `skills/view` →
     wire form.
   - `setup.py` `SKILL_STARTERS`: replace every tool reference with the wire name
     (`sqlite_execute`→`sqlite__execute`, `execute_script`→`compute__script`,
     `write_file`→`files__write`, etc.). Keep policy examples inside skills that instruct
     profile authoring in **canonical** form (they are config values, not tool calls).
10. **Config & policy globs**: `profiles/load.py` (`merge_tool_policy`) and
    `tools/groups.resolve_tier` normalize every allow/deny/eager/cold pattern with
    `normalize_pattern` so legacy config keeps matching. `config.py`
    `default_config_yaml` comments update examples to `web/*`, `sqlite/*`, `browser/*`,
    `media/*`. `lattice.yaml` comments only (values are `["*"]`/`[]`).
11. **Docs**: README Tools section → group tables (operator canonical names + note that the
    model sees `group__tool`); CLI/config examples; `doctor` reserved-group output.
12. **Tests**: update every module that asserts flat names, builds toolsets, or imports
    `lattice.tools.agent.*` (notably `test_toolsets_tiers.py`, `test_waist_hitl.py`,
    `test_core.py`, `test_scripts.py`, `test_browser.py`, `test_calculator.py`,
    `test_pdf_chart.py`, `test_action_ledger.py`, `test_state_fidelity.py`,
    `test_turn_trace.py`, `test_tool_results.py`, `test_harness_efficiency.py`,
    `test_hitl_prevalidate.py`, `test_user_tools.py`, `test_tool_search_bm25.py`,
    `test_live_status.py`, `test_integration.py`, `test_waist_hitl.py`); migrate imports to
    `lattice.tools.groups`. Assert wire names where the model/toolset is exercised and
    canonical names where policy/ledger is exercised.
13. **Eval**: update `tests/eval/corpus/*.jsonl` expectations to canonical names (the
    collector sees canonical `traced` names) and update forbidden/tool lists. Cassette
    replay will drift: recorded responses call flat tool names. Migrate cassettes
    (rewrite tool names + recompute `request_digest` with
    `lattice.eval.cassette.request_digest`, or re-record live) **or** rely on the compat
    alias mapping in the toolset. Confirm `uv run pytest -m eval` is green.

## Risks / caveats

- **Provider name grammar**: mitigated by `__` wire form + grammar validation; no `/`
  ever reaches a provider. Must assert wire names satisfy `^[a-zA-Z0-9_-]{1,64}$`.
- **Two-form confusion** is the top usability risk: model-facing text must use wire names,
  operator config must use canonical. Call this out in README and in the
  `skill-authoring`/`tool-authoring` starters.
- **Persisted state** (action ledgers, `turns.jsonl` from prior versions) holds flat names;
  apply `normalize_name` when reading/rendering so old sessions stay legible.
- **Eval cassettes** encode flat tool names; without migration `pytest -m eval` fails.
- **External MCP**: server/tool names never validated before (host is a stub). Add grammar
  + reserved-group checks; a server named `sqlite`/`web` must be rejected.
- **Cache stability**: keep group order and per-group eager ordering fixed so tool-definition
  bytes stay stable across turns; `build_toolsets` must emit all eager groups before every
  deferred group.

## Validation

- `uv run ruff check src tests && uv run ruff format --check src tests`
- `uv run pytest` (unit)
- `uv run pytest -m eval` (offline cassette replay; requires cassette migration)
- Unit assertions to add:
  - `wire_name(canonical)` matches `^[a-zA-Z0-9_-]{1,64}$` for all 37 tools.
  - Round-trip `canonical_name(wire_name(n)) == n`.
  - `normalize_pattern("sqlite_*") == "sqlite/*"`; legacy exact names map 1:1.
  - Model-visible tool defs use wire names; `enabled_tools`/policy use canonical.
  - Optional `uv run pytest -m integration` when keys are available to prove the provider
    accepts the wire names.
- Manual smoke: `uv run lattice doctor`, then `uv run lattice chat -p default` and confirm
  `search_tools` reveals/executes a deferred group tool.

## Out of scope

- No real MCP wire protocol or subprocess servers for built-ins.
- No change to `tools.mcp_defer*` semantics for external servers.
- No renaming of non-tool identifiers (skills, profiles, DBs, channels).
