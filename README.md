# Lattice

Thin-waist personal agent platform (Hermes-inspired): Pydantic AI loop, named profiles, HITL, full context compressor, multi-SQLite, rich TUI + Telegram, MCP bridge, mem0/Qdrant.

See [docs/PLAN.md](docs/PLAN.md) for the full product plan.

## Quick start

```bash
uv sync --extra dev
# Project .env (also supported: .lattice/.env)
# OPENROUTER_API_KEY=...
# OPENROUTER_MODEL=...
# TELEGRAM_TOKEN=...
# TELEGRAM_CHAT_ID=...
# or OPENAI_API_KEY=...
uv run lattice init
uv run lattice doctor
uv run lattice chat -p default
uv run lattice chat --tui -p finance
```

Gateway (Telegram + scheduler):

```bash
# set TELEGRAM_TOKEN / TELEGRAM_CHAT_ID in .env (or .lattice/config.yaml)
uv run lattice gateway
```

## Layout

- Waist: `src/lattice/{turn,agent_app,prompt,session,config,events,cli}.py`
- Domains: `channel/`, `hitl/`, `context/`, `tools/`, `mcp/`, `skills/`, `profiles/`, `memory/`, `sqlite/`, `providers/`, `scheduler/`
- User data: `<project>/.lattice/` (override with `LATTICE_HOME`)

## Dev

```bash
uv run ruff check src tests
uv run ruff format src tests
uv run pytest                 # unit only (default)
uv run pytest -m integration  # live LLM/Tavily when .env keys present
```
