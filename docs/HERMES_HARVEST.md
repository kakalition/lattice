# Hermes → Lattice harvest

Reference inventory from [`references/hermes-agent/`](../references/hermes-agent/). Lattice product decisions stay in [`PLAN.md`](PLAN.md). **Do not vendor** Hermes skill trees; rewrite any adapt for Lattice tool names and `~/.lattice/`.

## Tools

### Already in Lattice

| Hermes | Lattice | Notes |
|--------|---------|-------|
| `terminal` | `shell` | HITL-gated when dangerous |
| `read_file` / `write_file` / `patch` / `search_files` | same (+ `edit_file`) | Keep both write (full) and edit (surgical) |
| `web_search` / `web_extract` | `web_search` / `web_fetch` | Tavily + httpx |
| `skills_list` / `skill_view` | same | Progressive disclosure |
| `clarify` / `todo_list` / `session_search` | `clarify` / `todo` / `session_search` | |
| `memory` | `memory_search` / `add` / `update` / `forget` | Split API |
| Tool Search / MCP | `tool_search` / `tool_describe` / `tool_invoke` | Lite bridge |
| — | `sqlite_*` suite | Lattice-only |
| `cronjob_manage` | `scheduler/` domain | Not a model tool |

### Optional later (deferred — not in this harvest)

| Hermes | Why deferred |
|--------|----------------|
| `process_manage` | Background shell job control |
| `skill_manage` | Create/edit skills at runtime |
| `vision_analyze` | Aux vision for inbound images |

### Skip (v1)

Browser stack (`browser_*`, `browser_exec`), `computer_use`, `execute_code`, `delegate_task`, `kanban_*`, Home Assistant, Discord/Feishu/Yuanbao bots, `desktop_ui` / `desktop_project`, video gen/analyze, `x_search`, `manage_connections`, A2A plugins, first-party optional MCP zoo (use live MCP instead).

## Skills

### Keep (Lattice starters)

`session-hygiene`, `safe-shell`, `web-research`, `sqlite-admin`

### Adapted (Lattice-native rewrites)

| Lattice skill | Inspired by Hermes | Notes |
|---------------|--------------------|-------|
| `cited-research` | `skills/research/grounded-citations` | No Hermes ledger scripts; cite via `web_*` |
| `weekly-review` | `skills/productivity/weekly-review-planning` | Memory + todo + scheduler; no Google/Obsidian deps |
| `office-xlsx` | `skills/productivity/xlsx` | Guidance only; use `shell` + openpyxl if installed |

### Explicitly skipped (user)

- `stocks` (`optional-skills/finance/stocks`)
- `obsidian` (`skills/note-taking/obsidian`)

### Skip trees

Entire `optional-skills/mlops/`, Hermes-platform skills (`hermes-agent`, skill-authoring, desktop DOM, s6, `honcho`, openclaw-migration), skills that require NOT-ported tools, security abuse skills, blockchain/gaming/payments vertical zoo, Skills Hub / org mirrors.

### Later adapts (coding / research profiles)

`arxiv`, `systematic-debugging`, `github`, `document-to-action-items`

## edit_file vs write_file (locked)

- **`write_file`**: create or overwrite entire file
- **`edit_file`**: unique search-replace patch (Hermes `patch` analogue)

Keep both.

## Sources

- Hermes: `toolsets.py` (`_HERMES_CORE_TOOLS`), `tools/`, `skills/`, `optional-skills/`
- Lattice: `src/lattice/agent_app.py` `CORE_TOOL_NAMES`, `src/lattice/setup.py` `SKILL_STARTERS`
