"""Application configuration via pydantic-settings + YAML."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field
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
    # fnmatch globs matched against tool names; override each module's default TIER.
    # ``cold`` wins when a name matches both.
    eager: list[str] = Field(default_factory=list)
    cold: list[str] = Field(default_factory=list)


class ChannelToolsConfig(BaseModel):
    allow: list[str] = Field(default_factory=lambda: ["*"])
    deny: list[str] = Field(default_factory=list)


class TelegramConfig(BaseModel):
    token: str | None = None
    allowlist: list[int] = Field(default_factory=list)
    tools: ChannelToolsConfig = Field(default_factory=ChannelToolsConfig)


class MemoryConfig(BaseModel):
    """mem0 backend tuning.

    mem0 calls an LLM to extract facts from turns. Reasoning models spend most of
    the token budget on hidden reasoning, so the non-reasoning parameter set
    (which sends ``max_tokens``) truncates their JSON and extraction silently
    yields nothing. mem0 auto-detects only the o1/o3/gpt-5 families, so models
    like mercury-2.5 need an explicit override.
    """

    # None = auto-detect from the model name; True/False force the behaviour.
    is_reasoning_model: bool | None = None
    # Verify the memory round-trip at boot (write a private token, search it back).
    # Costs one embedding and no LLM call, which is why it is on by default.
    self_check: bool = True


class AgentConfig(BaseModel):
    workspace: Path | None = None
    primary_model: str = "openai:gpt-4o"
    secondary_model: str = "openai:gpt-4o-mini"
    auxiliary_model: str = "openai:gpt-4o-mini"
    secondary_max_iterations: int = 8
    iteration_budget: int = 40
    hitl_timeout_seconds: int = 600
    context_pressure_ratio: float = 0.5
    protect_last_n: int = 20
    idle_watchdog_seconds: int = 600
    # Explicit prompt caching for capable OpenRouter models (Anthropic/Gemini).
    # No-op for providers without explicit cache control (OpenAI/DeepSeek auto-cache).
    prompt_cache: bool = True
    prompt_cache_ttl: Literal["5m", "1h"] = "5m"


class ProviderConfig(BaseModel):
    api_key: str | None = None
    base_url: str | None = None
    fallback_model: str | None = None


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
    provider: ProviderConfig = Field(default_factory=ProviderConfig)
    tools: ToolsConfig = Field(default_factory=ToolsConfig)
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
  secondary_model: inception/mercury-2.5
  auxiliary_model: inception/mercury-2.5
  secondary_max_iterations: 8
  iteration_budget: 40
  hitl_timeout_seconds: 600
  workspace: null
  # Explicit prompt caching for OpenRouter Anthropic/Gemini models.
  # prompt_cache: true
  # prompt_cache_ttl: 5m   # 5m | 1h (1h is Anthropic-only)

provider:
  fallback_model: inception/mercury-2.5

tools:
  allow: ["*"]
  deny: []
  mcp_defer: auto
  # Optional: override which tools ship eagerly vs. behind tool search.
  # Glob patterns; `cold` wins on conflict. Empty = use each tool's built-in tier.
  eager: []   # e.g. ["web_*", "sqlite_*"]
  cold: []    # e.g. ["browser_*", "metric_*"]

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
