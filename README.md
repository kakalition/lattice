# Lattice

Thin-waist personal agent platform (Hermes-inspired): Pydantic AI loop, named profiles, HITL, full context compressor, multi-SQLite, rich TUI + Telegram, MCP bridge, mem0/Qdrant.

See [docs/PLAN.md](docs/PLAN.md) for the full product plan.

## Quick start

```bash
uv sync --extra dev
# Project .env — secrets only (copy from .env.example)
# Models / timezone / tools → lattice.yaml
uv run lattice init
uv run lattice doctor
# First chat/gateway auto-runs one-time setup (e.g. Playwright Chromium fallback).
# Browser prefers system Chrome + .lattice/browser/profile; see browser: in lattice.yaml
uv run lattice chat -p default
uv run lattice chat --tui
```

Backup / restore runtime home (``.lattice`` → portable archive):

```bash
uv run lattice backup                 # → ./.lattice-<utc>.tar.gz
uv run lattice backup -o ~/lattice.tgz
uv run lattice restore ~/lattice.tgz  # empty home only
uv run lattice restore ~/lattice.tgz --force  # displaces existing home to .lattice.bak.<utc>
```

Gateway (Telegram + scheduler):

```bash
cp .env.example .env   # then fill secrets
# models and other knobs in lattice.yaml
uv run lattice gateway
```

## Layout

- Waist: `src/lattice/{turn,agent_app,prompt,session,config,events,cli}.py`
- Domains: `channel/`, `hitl/`, `context/`, `tools/`, `mcp/`, `skills/`, `profiles/`, `memory/`, `sqlite/`, `providers/`, `scheduler/`
- **`lattice.yaml`** — configurable non-secrets (models, timezone, tools, budgets)
- **`.env`** — secrets only (API keys, Telegram token)
- **`.lattice/`** — runtime data only (state, logs, qdrant, jobs)

### Models (`lattice.yaml`)

| Key | Role |
|-----|------|
| `agent.primary_model` | The single model that runs every turn |

A profile may override the model with `primary_model` (or legacy `model`), and the sticky
`/model [id|clear]` command overrides it per channel+user; exactly one model is active per
turn. Context compression and mem0 fact extraction run on that same resolved model.

Prompt caching: the primary keeps a stable system prefix (SOUL/USER/skills) and passes session `message_history`; volatile notices go in the user tail. On OpenRouter (`openrouter.ai` base URL) Lattice builds `OpenRouterModel` and, for Anthropic/Gemini models, marks stable instructions, tool definitions, and the last replayed user prompt with an explicit `CachePoint` boundary; `agent.prompt_cache` (default `true`) and `agent.prompt_cache_ttl` (`5m`/`1h`) control this. Other endpoints use provider auto-cache, and each turn persists `cache_read_tokens`/`cache_write_tokens`/`cache_hit_ratio` in `sessions.usage_json` and the turn log. On OpenRouter every request also carries a body-level `session_id` (sticky provider routing), which keeps the auto-cache locality for providers such as DeepSeek that expose no explicit `CachePoint`.


## Dev

```bash
uv run ruff check src tests
uv run ruff format src tests
uv run pytest                 # unit only (default)
uv run pytest -m integration  # live LLM/Tavily when .env keys present
```
