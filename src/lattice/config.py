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
    """Load settings from ~/.lattice/config.yaml then env overrides."""
    root = home or lattice_home()
    data: dict[str, Any] = {"home": root}
    config_path = root / "config.yaml"
    if config_path.is_file():
        loaded = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        if isinstance(loaded, dict):
            data = _deep_merge(data, loaded)
    env_path = root / ".env"
    settings = LatticeSettings(_env_file=env_path if env_path.is_file() else None, **data)
    return settings


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
