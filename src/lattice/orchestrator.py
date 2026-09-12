"""Whole-turn orchestrator: classify a turn, then route worker vs. primary.

The classifier is a tool-free agent that shares the primary's byte-stable system
prompt so provider-side prefix caching (and OpenRouter sticky routing) covers the
expensive instructions on every turn. It answers with a small JSON object parsed
from text — deliberately provider-agnostic, not structured tool output. Any
failure (timeout, transport error, invalid JSON) is fail-safe: route HIGH.
"""

from __future__ import annotations

import json
import logging
from enum import StrEnum
from typing import Any, cast

from pydantic import BaseModel, ValidationError, field_validator
from pydantic_ai import Agent
from pydantic_ai.settings import ModelSettings
from pydantic_ai.usage import RunUsage

from lattice.config import LatticeSettings
from lattice.providers.caching import prompt_cache_settings, session_routing_settings
from lattice.providers.openai_compat import build_openai_model
from lattice.tools.deadline import with_deadline

logger = logging.getLogger("lattice.orchestrator")

ROUTE_MARKER = "[route]"


class Complexity(StrEnum):
    LOW = "LOW"
    HIGH = "HIGH"


class RoutingDecision(BaseModel):
    complexity: Complexity
    task: str = ""
    reason: str = ""

    @field_validator("complexity", mode="before")
    @classmethod
    def _coerce_complexity(cls, value: Any) -> Any:
        if isinstance(value, str):
            upper = value.strip().upper()
            if upper in Complexity._value2member_map_:
                return upper
        return value


def parse_decision(text: str | None) -> RoutingDecision | None:
    """Parse the classifier's JSON reply; ``None`` on any malformed output."""
    if not text:
        return None
    body = text.strip()
    if body.startswith("```"):
        lines = body.splitlines()
        lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        body = "\n".join(lines).strip()
    start = body.find("{")
    end = body.rfind("}")
    if start == -1 or end == -1 or end < start:
        return None
    try:
        data = json.loads(body[start : end + 1])
    except (TypeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    try:
        return RoutingDecision.model_validate(data)
    except ValidationError:
        return None


_classifier_cache: dict[tuple[str, str, str], Agent[Any, str]] = {}


def clear_classifier_cache() -> None:
    _classifier_cache.clear()


def _classifier_agent(
    settings: LatticeSettings, model_id: str, system_prompt: str, session_id: str
) -> Agent[Any, str]:
    key = (model_id, system_prompt, session_id)
    cached = _classifier_cache.get(key)
    if cached is not None:
        return cached
    model_obj = build_openai_model(settings, model_id)
    model_settings: dict[str, Any] = {
        **prompt_cache_settings(settings, model_obj),
        **session_routing_settings(settings, session_id),
    }
    cap = settings.agent.orchestrator.classifier_max_tokens
    if cap is not None:
        model_settings["max_tokens"] = cap
    agent: Agent[Any, str] = Agent(
        model_obj,
        system_prompt=system_prompt,
        model_settings=cast(ModelSettings, model_settings),
    )
    _classifier_cache[key] = agent
    return agent


async def decide_route(
    settings: LatticeSettings,
    model_id: str,
    system_prompt: str,
    user_text: str,
    *,
    session_id: str,
) -> tuple[RoutingDecision, RunUsage]:
    """Classify ``user_text`` as LOW/HIGH. Always returns a decision (HIGH on failure)."""
    try:
        agent = _classifier_agent(settings, model_id, system_prompt, session_id)
        result = await with_deadline(
            agent.run(f"{ROUTE_MARKER}\n{user_text}"),
            seconds=float(settings.agent.idle_watchdog_seconds),
            label="classifier",
        )
        usage = getattr(result, "usage", None) or RunUsage()
        decision = parse_decision(str(result.output))
        if decision is None:
            return RoutingDecision(complexity=Complexity.HIGH, reason="unparseable"), usage
        return decision, usage
    except Exception as exc:  # noqa: BLE001 — classifier failure must never break a turn
        logger.debug("classifier failed, routing HIGH: %s", exc)
        return RoutingDecision(complexity=Complexity.HIGH, reason="classifier error"), RunUsage()
