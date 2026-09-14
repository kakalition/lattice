"""Application configuration via pydantic-settings + YAML."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from lattice.paths import lattice_home, project_root, user_config_path


class McpDeferMode(StrEnum):
    ALWAYS = "always"
    AUTO = "auto"
    NEVER = "never"


class ToolTier(StrEnum):
    """How a tool reaches the model: always on the wire, or hidden behind tool search."""

    EAGER = "eager"
    COLD = "cold"


class SqliteDatabaseConfig(BaseModel):
    path: str
    read_only: bool = False


class SqliteConfig(BaseModel):
    databases: dict[str, SqliteDatabaseConfig] = Field(default_factory=dict)
    query_row_limit: int = 500
    query_timeout_ms: int = 5000
    # Connection tuning applied on every connect (see sqlite/pragmas.py).
    busy_timeout_ms: int = 5000
    mmap_size_bytes: int = 30_000_000_000


class ToolsConfig(BaseModel):
    allow: list[str] = Field(default_factory=lambda: ["*"])
    deny: list[str] = Field(default_factory=list)
    mcp_defer: McpDeferMode = McpDeferMode.AUTO
    mcp_defer_threshold: int = 8
    # fnmatch globs matched against canonical tool names (`group/leaf`); override
    # each tool's default TIER. Legacy flat names and `prefix_*` globs still match.
    # ``cold`` wins when a name matches both.
    eager: list[str] = Field(default_factory=list)
    cold: list[str] = Field(default_factory=list)
    # Local deferred-tool ranking algorithm (not the provider-native strategy).
    # ``bm25`` runs the in-process scorer; ``keywords`` keeps pydantic-ai's overlap.
    search_strategy: Literal["bm25", "keywords"] = "bm25"
    # Trim BM25 matches scoring below this fraction of the top score. The best
    # match is always kept; ``0`` disables trimming.
    search_min_ratio: float = Field(default=0.35, ge=0.0, le=1.0)


class ChannelToolsConfig(BaseModel):
    allow: list[str] = Field(default_factory=lambda: ["*"])
    deny: list[str] = Field(default_factory=list)


class McpServerConfig(BaseModel):
    """One external MCP server: a stdio command or a streamable-HTTP URL."""

    name: str
    command: str | None = None
    args: list[str] = Field(default_factory=list)
    url: str | None = None
    env: dict[str, str] = Field(default_factory=dict)
    cwd: str | None = None
    enabled: bool = True

    @model_validator(mode="after")
    def _exactly_one_transport(self) -> McpServerConfig:
        if bool((self.command or "").strip()) == bool((self.url or "").strip()):
            raise ValueError("mcp server must set exactly one of command or url")
        return self


class McpConfig(BaseModel):
    """External MCP servers and their connection timeouts."""

    enabled: bool = True
    servers: list[McpServerConfig] = Field(default_factory=list)
    # Discovery/initialize budget per server; calls get the longer budget.
    connect_timeout_seconds: float = 20.0
    call_timeout_seconds: float = 60.0


class TelegramConfig(BaseModel):
    token: str | None = None
    allowlist: list[int] = Field(default_factory=list)
    tools: ChannelToolsConfig = Field(default_factory=ChannelToolsConfig)


class MemoryConfig(BaseModel):
    """mem0 backend tuning.

    mem0 can call an LLM to extract facts from turns. That extraction is fragile
    with models that ignore JSON mode (malformed JSON drops the whole batch) and
    adds a round-trip to every turn, so it is **off by default**: turns are stored
    with an embedding only. Enable ``extract_on_turn`` for automatic fact
    extraction when the primary model emits strict JSON reliably.

    Reasoning models spend most of the token budget on hidden reasoning, so the
    non-reasoning parameter set (which sends ``max_tokens``) truncates their JSON
    and extraction silently yields nothing. mem0 auto-detects only the
    o1/o3/gpt-5 families, so models like mercury-2.5 need an explicit override.
    """

    # Run mem0's LLM fact-extraction each turn. Off = embed + store the transcript.
    extract_on_turn: bool = False
    # None = auto-detect from the model name; True/False force the behaviour.
    is_reasoning_model: bool | None = None
    # Verify the memory round-trip at boot (write a private token, search it back).
    # Costs one embedding and no LLM call, which is why it is on by default.
    self_check: bool = True
    # Bound each background memory job so one hung write cannot starve the queue.
    sync_timeout_seconds: int = 60
    # Cap the pre-model memory recall. 0 disables it (recall runs unbounded, as
    # before); a positive value drops memories and logs a warning on timeout so a
    # slow embed cannot delay the whole turn.
    search_timeout_seconds: float = 0.0


class ObservabilityConfig(BaseModel):
    """Structured telemetry beside the human log."""

    # Append one JSON line per turn to <home>/logs/turns.jsonl.
    turn_record: bool = True


class AgentConfig(BaseModel):
    workspace: Path | None = None
    primary_model: str = "openai:gpt-4o"
    iteration_budget: int = 60
    hitl_timeout_seconds: int = 600
    context_pressure_ratio: float = 0.5
    protect_last_n: int = 20
    # Total wall-clock deadline for a turn. Retained under the legacy name so
    # existing lattice.yaml files keep working; ``turn_timeout_seconds`` is the
    # intended spelling and is accepted as an input alias.
    idle_watchdog_seconds: int = 600
    # Per-model-request timeout applied via ModelSettings (provider permitting).
    request_timeout_seconds: int = 120
    # Optional cheaper model for turn summarization; None = the primary model.
    summarizer_model: str | None = None
    # Explicit prompt caching for capable OpenRouter models (Anthropic/Gemini).
    # No-op for providers without explicit cache control (OpenAI/DeepSeek auto-cache).
    prompt_cache: bool = True
    prompt_cache_ttl: Literal["5m", "1h"] = "5m"
    # Replay bounded evidence (path + hash + snippet) for recent reads/queries in
    # the volatile tail. On by default so a later turn need not re-read the same
    # file; stays in ``[notice]`` lines (digest-scrubbed) and is tightly capped.
    # Opt out with ``replay_evidence: false``.
    replay_evidence: bool = True
    # Model context window override; None = resolve from the model profile.
    context_window_tokens: int | None = None

    @property
    def turn_timeout_seconds(self) -> int:
        return self.idle_watchdog_seconds

    @model_validator(mode="before")
    @classmethod
    def _alias_turn_timeout(cls, data: Any) -> Any:
        if (
            isinstance(data, dict)
            and "turn_timeout_seconds" in data
            and "idle_watchdog_seconds" not in data
        ):
            data = {**data, "idle_watchdog_seconds": data["turn_timeout_seconds"]}
        return data


class ProviderConfig(BaseModel):
    api_key: str | None = None
    base_url: str | None = None


class BrowserChannel(StrEnum):
    AUTO = "auto"  # prefer system Chrome, fall back to Chromium
    CHROME = "chrome"
    CHROMIUM = "chromium"


class BrowserConfig(BaseModel):
    """Playwright browser_interact / browser_snapshot settings."""

    channel: BrowserChannel = BrowserChannel.AUTO
    headed: bool = False
    persistent_profile: bool = True
    # Relative to Lattice home unless absolute; default .lattice/browser/profile
    profile_dir: str | None = None
    humanize: bool = True
    type_delay_ms_min: int = 25
    type_delay_ms_max: int = 75


class ScriptsConfig(BaseModel):
    """execute_script sandbox settings (prefer bwrap)."""

    languages: list[str] = Field(default_factory=lambda: ["python", "node", "bash"])
    timeout_seconds: float = 60.0
    max_timeout_seconds: float = 300.0
    allow_network: bool = False
    # When true, refuse to run without bubblewrap. Default false so macOS soft-sandbox works.
    require_bwrap: bool = False


class LatticeSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="LATTICE_",
        env_nested_delimiter="__",
        extra="ignore",
    )

    home: Path = Field(default_factory=lattice_home)
    agent: AgentConfig = Field(default_factory=AgentConfig)
    observability: ObservabilityConfig = Field(default_factory=ObservabilityConfig)
    provider: ProviderConfig = Field(default_factory=ProviderConfig)
    tools: ToolsConfig = Field(default_factory=ToolsConfig)
    mcp: McpConfig = Field(default_factory=McpConfig)
    browser: BrowserConfig = Field(default_factory=BrowserConfig)
    scripts: ScriptsConfig = Field(default_factory=ScriptsConfig)
    sqlite: SqliteConfig = Field(default_factory=SqliteConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    telegram: TelegramConfig = Field(default_factory=TelegramConfig)
    tavily_api_key: str | None = None
    default_profile: str = "default"
    queue_depth: int = 8
    timezone: str = ""  # IANA name; empty → detect / read config


def _deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for key, value in overlay.items():
        if key in out and isinstance(out[key], dict) and isinstance(value, dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def _scrub_secrets(data: dict[str, Any]) -> dict[str, Any]:
    """Drop secret fields so YAML can never supply tokens/keys."""
    out = dict(data)
    out.pop("tavily_api_key", None)
    if isinstance(out.get("provider"), dict):
        provider = dict(out["provider"])
        provider.pop("api_key", None)
        out["provider"] = provider
    if isinstance(out.get("telegram"), dict):
        telegram = dict(out["telegram"])
        telegram.pop("token", None)
        out["telegram"] = telegram
    if isinstance(out.get("mcp"), dict):
        mcp = dict(out["mcp"])
        servers = mcp.get("servers")
        if isinstance(servers, list):
            mcp["servers"] = [
                {**srv, "env": {}} if isinstance(srv, dict) and srv.get("env") else srv
                for srv in servers
            ]
        out["mcp"] = mcp
    return out


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return loaded if isinstance(loaded, dict) else {}


def merge_yaml_into(path: Path, updates: dict[str, Any]) -> Path:
    """Create/merge keys into a YAML config file. Returns path written."""
    path.parent.mkdir(parents=True, exist_ok=True)
    data = _scrub_secrets(_load_yaml(path))
    data = _deep_merge(data, _scrub_secrets(updates))
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return path


def _normalize_agent_keys(data: dict[str, Any]) -> dict[str, Any]:
    """Map legacy agent.model → primary_model; drop unknown secret-like fields already scrubbed."""
    out = dict(data)
    agent = dict(out.get("agent") or {})
    if "primary_model" not in agent and agent.get("model"):
        agent["primary_model"] = agent.pop("model")
    elif "model" in agent:
        agent.pop("model", None)
    out["agent"] = agent
    return out


def load_settings(home: Path | None = None) -> LatticeSettings:
    """Load non-secret lattice.yaml + secrets exclusively from .env."""
    _load_dotenv_files(home)
    root = home or lattice_home()
    data: dict[str, Any] = {"home": root}
    # Visible project config only (legacy .lattice/config.yaml ignored for settings)
    data = _deep_merge(data, _scrub_secrets(_load_yaml(user_config_path(home))))
    data = _normalize_agent_keys(data)
    data = _apply_compat_env(data)
    # Do not pass a YAML _env_file for secrets — project .env already loaded into os.environ
    settings = LatticeSettings(**data)
    if not (settings.timezone or "").strip():
        from lattice.timeutil import resolve_timezone

        settings.timezone = resolve_timezone(home)
    return settings


def _load_dotenv_files(home: Path | None = None) -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    # Project .env is the only secrets source (override=False keeps shell exports)
    load_dotenv(project_root() / ".env", override=False)
    load_dotenv(Path.cwd() / ".env", override=False)


def _apply_compat_env(data: dict[str, Any]) -> dict[str, Any]:
    """Inject secrets from .env only. Models and other knobs come from lattice.yaml."""
    import os

    out = dict(data)
    provider = dict(out.get("provider") or {})
    agent = dict(out.get("agent") or {})
    telegram = dict(out.get("telegram") or {})

    key = (
        os.environ.get("LATTICE_PROVIDER__API_KEY")
        or os.environ.get("OPENAI_API_KEY")
        or os.environ.get("OPENROUTER_API_KEY")
    )
    if key:
        provider["api_key"] = key

    base = os.environ.get("LATTICE_PROVIDER__BASE_URL") or os.environ.get("OPENAI_BASE_URL")
    if not base and os.environ.get("OPENROUTER_API_KEY"):
        base = "https://openrouter.ai/api/v1"
    if base:
        provider["base_url"] = base

    tok = os.environ.get("TELEGRAM_TOKEN") or os.environ.get("LATTICE_TELEGRAM__TOKEN")
    if tok:
        telegram["token"] = tok

    chat = os.environ.get("TELEGRAM_CHAT_ID") or os.environ.get("TELEGRAM_ALLOWLIST")
    if chat:
        import contextlib

        with contextlib.suppress(ValueError):
            telegram["allowlist"] = [int(x.strip()) for x in chat.split(",") if x.strip()]

    out["provider"] = provider
    out["agent"] = agent
    out["telegram"] = telegram

    tav = os.environ.get("LATTICE_TAVILY_API_KEY") or os.environ.get("TAVILY_API_KEY")
    if tav:
        out["tavily_api_key"] = tav
    else:
        out.pop("tavily_api_key", None)
    return out


def default_config_yaml() -> str:
    """Non-secret operator defaults (including models). Secrets → .env only."""
    return """\
# Non-secret Lattice settings (models, timezone, tools, …).
# Secrets → project .env only:
#   OPENROUTER_API_KEY / TELEGRAM_TOKEN / TELEGRAM_CHAT_ID / TAVILY_API_KEY
# Runtime data → .lattice/

timezone: Asia/Jakarta

agent:
  primary_model: deepseek/deepseek-v4.1-flash
  iteration_budget: 60
  hitl_timeout_seconds: 600
  workspace: null
  # Total turn deadline; the legacy alias `idle_watchdog_seconds` still works.
  turn_timeout_seconds: 600
  # Per-model-request timeout (provider permitting).
  request_timeout_seconds: 120
  # Optional cheaper model for turn summarization; omit to use the primary model.
  # summarizer_model: openai:gpt-4o-mini
  # Explicit prompt caching for OpenRouter Anthropic/Gemini models.
  # prompt_cache: true
  # prompt_cache_ttl: 5m   # 5m | 1h (1h is Anthropic-only)
  # Replay bounded evidence for recent reads/queries in the volatile tail
  # (digest-scrubbed, ~3KB cap). On by default; set false to disable.
  # replay_evidence: true
  # Model context window override; null = resolve from the model profile.
  # context_window_tokens: null

observability:
  # Append one structured JSON line per turn to <home>/logs/turns.jsonl.
  turn_record: true

memory:
  # Run mem0's LLM fact-extraction on every turn. Off (default) stores the turn
  # transcript with an embedding only — no LLM call, no extraction-JSON failures.
  extract_on_turn: false
  # None = auto-detect; True/False force mem0's reasoning-model parameter set.
  is_reasoning_model: null
  # Verify the memory round-trip at boot (embedding only, no LLM).
  self_check: true
  # Bound each background memory write so a hung job cannot starve the queue.
  sync_timeout_seconds: 60
  # Cap pre-model memory recall; 0 = unbounded (default). A positive value drops
  # memories and logs a warning on timeout so slow embeddings cannot delay turns.
  # search_timeout_seconds: 0.0

tools:
  allow: ["*"]
  deny: []
  mcp_defer: auto
  # Local deferred-tool ranking: bm25 (default) | keywords.
  search_strategy: bm25
  # Drop BM25 matches scoring below this fraction of the best match; 0 disables.
  search_min_ratio: 0.35
  # Optional: override which tools ship eagerly vs. behind tool search.
  # Glob patterns match canonical `group/leaf` names; `cold` wins on conflict.
  # Empty = use each tool's built-in tier.
  eager: []   # e.g. ["web/*", "sqlite/*"]
  cold: []    # e.g. ["browser/*", "media/*"]

mcp:
  enabled: true
  # External MCP servers. Each needs exactly one transport: `command` (stdio)
  # or `url` (streamable HTTP). Tools are exposed to the model as
  # `server__tool` and matched in config as `server/tool`; server names may not
  # reuse a built-in group name.
  # servers:
  #   - name: filesystem
  #     command: npx
  #     args: ["-y", "@modelcontextprotocol/server-filesystem", "."]
  #   - name: remote
  #     url: https://example.com/mcp
  servers: []

browser:
  channel: auto          # auto | chrome | chromium (auto prefers system Chrome)
  headed: false          # true = visible window (stronger vs bot checks)
  persistent_profile: true
  profile_dir: null      # default: .lattice/browser/profile
  humanize: true         # mouse move + delayed typing on click/type
  type_delay_ms_min: 25
  type_delay_ms_max: 75

scripts:
  languages: [python, node, bash]
  timeout_seconds: 60
  max_timeout_seconds: 300
  allow_network: false
  require_bwrap: false   # true = refuse without bubblewrap (recommended on Linux)

sqlite:
  databases: {}
  query_row_limit: 500
  query_timeout_ms: 5000
  busy_timeout_ms: 5000
  mmap_size_bytes: 30000000000

telegram:
  tools:
    allow: ["*"]
    deny: []

default_profile: default
"""
