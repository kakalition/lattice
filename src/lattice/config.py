"""Application configuration via pydantic-settings + YAML."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from lattice.paths import lattice_home


class McpDeferMode(StrEnum):
    ALWAYS = "always"
    AUTO = "auto"
    NEVER = "never"


class SqliteDatabaseConfig(BaseModel):
    path: str
    read_only: bool = False


class SqliteConfig(BaseModel):
    databases: dict[str, SqliteDatabaseConfig] = Field(default_factory=dict)
    query_row_limit: int = 500
    query_timeout_ms: int = 5000


class ToolsConfig(BaseModel):
    allow: list[str] = Field(default_factory=lambda: ["*"])
    deny: list[str] = Field(default_factory=list)
    mcp_defer: McpDeferMode = McpDeferMode.AUTO
    mcp_defer_threshold: int = 8


class ChannelToolsConfig(BaseModel):
    allow: list[str] = Field(default_factory=lambda: ["*"])
    deny: list[str] = Field(default_factory=list)


class TelegramConfig(BaseModel):
    token: str | None = None
    allowlist: list[int] = Field(default_factory=list)
    tools: ChannelToolsConfig = Field(default_factory=ChannelToolsConfig)


class AgentConfig(BaseModel):
    workspace: Path | None = None
    model: str = "openai:gpt-4o"
    auxiliary_model: str = "openai:gpt-4o-mini"
    iteration_budget: int = 40
    hitl_timeout_seconds: int = 600
    context_pressure_ratio: float = 0.5
    protect_last_n: int = 20
    idle_watchdog_seconds: int = 600


class ProviderConfig(BaseModel):
    api_key: str | None = None
    base_url: str | None = None
    fallback_model: str | None = None


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
    sqlite: SqliteConfig = Field(default_factory=SqliteConfig)
    telegram: TelegramConfig = Field(default_factory=TelegramConfig)
    tavily_api_key: str | None = None
    default_profile: str = "default"
    queue_depth: int = 8


def _deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for key, value in overlay.items():
        if key in out and isinstance(out[key], dict) and isinstance(value, dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def load_settings(home: Path | None = None) -> LatticeSettings:
    """Load settings from YAML + project/.lattice .env + process env."""
    _load_dotenv_files(home)
    root = home or lattice_home()
    data: dict[str, Any] = {"home": root}
    config_path = root / "config.yaml"
    if config_path.is_file():
        loaded = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        if isinstance(loaded, dict):
            data = _deep_merge(data, loaded)
    data = _apply_compat_env(data)
    env_path = root / ".env"
    settings = LatticeSettings(_env_file=env_path if env_path.is_file() else None, **data)
    return settings


def _load_dotenv_files(home: Path | None = None) -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    # Project cwd .env first (dev), then ~/.lattice/.env
    load_dotenv(Path.cwd() / ".env", override=False)
    root = home or lattice_home()
    load_dotenv(root / ".env", override=False)


def _apply_compat_env(data: dict[str, Any]) -> dict[str, Any]:
    """Map common third-party env names into Lattice settings shape."""
    import os

    out = dict(data)
    provider = dict(out.get("provider") or {})
    agent = dict(out.get("agent") or {})
    telegram = dict(out.get("telegram") or {})

    if not provider.get("api_key"):
        key = (
            os.environ.get("LATTICE_PROVIDER__API_KEY")
            or os.environ.get("OPENAI_API_KEY")
            or os.environ.get("OPENROUTER_API_KEY")
        )
        if key:
            provider["api_key"] = key
    if not provider.get("base_url"):
        base = os.environ.get("LATTICE_PROVIDER__BASE_URL") or os.environ.get("OPENAI_BASE_URL")
        if not base and os.environ.get("OPENROUTER_API_KEY"):
            base = "https://openrouter.ai/api/v1"
        if base:
            provider["base_url"] = base
    if (
        os.environ.get("OPENROUTER_MODEL")
        and os.environ.get("OPENROUTER_API_KEY")
        and agent.get("model") in (None, "openai:gpt-4o")
    ):
        agent["model"] = os.environ["OPENROUTER_MODEL"]
        agent.setdefault("auxiliary_model", os.environ["OPENROUTER_MODEL"])
    if not telegram.get("token"):
        tok = os.environ.get("TELEGRAM_TOKEN") or os.environ.get("LATTICE_TELEGRAM__TOKEN")
        if tok:
            telegram["token"] = tok
    if not telegram.get("allowlist"):
        chat = os.environ.get("TELEGRAM_CHAT_ID") or os.environ.get("TELEGRAM_ALLOWLIST")
        if chat:
            import contextlib

            with contextlib.suppress(ValueError):
                telegram["allowlist"] = [int(x.strip()) for x in chat.split(",") if x.strip()]

    out["provider"] = provider
    out["agent"] = agent
    out["telegram"] = telegram
    if not out.get("tavily_api_key"):
        tav = os.environ.get("LATTICE_TAVILY_API_KEY") or os.environ.get("TAVILY_API_KEY")
        if tav:
            out["tavily_api_key"] = tav
    return out


def default_config_yaml() -> str:
    return """\
# Lattice configuration
agent:
  model: openai:gpt-4o
  auxiliary_model: openai:gpt-4o-mini
  iteration_budget: 40
  hitl_timeout_seconds: 600
  workspace: null

provider:
  # api_key: set via LATTICE_PROVIDER__API_KEY or OPENAI_API_KEY
  base_url: null
  fallback_model: null

tools:
  allow: ["*"]
  deny: []
  mcp_defer: auto

sqlite:
  databases: {}
  query_row_limit: 500
  query_timeout_ms: 5000

telegram:
  token: null
  allowlist: []
  tools:
    allow: ["*"]
    deny: []

tavily_api_key: null
default_profile: default
"""
