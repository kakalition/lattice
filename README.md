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
uv run lattice chat -p default
uv run lattice chat --tui -p finance
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

## Dev

```bash
uv run ruff check src tests
uv run ruff format src tests
uv run pytest                 # unit only (default)
uv run pytest -m integration  # live LLM/Tavily when .env keys present
```
