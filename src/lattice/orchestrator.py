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
import re
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

# Reasons ``decide_route`` assigns to a fail-safe HIGH. Turn sources use these to
# label the route as ``fallback`` rather than a real classifier decision.
CLASSIFIER_FALLBACK_REASONS = frozenset({"unparseable", "classifier error"})


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


# Heuristic pre-gate boundaries. Module constants so tests pin the exact limits.
HEURISTIC_HIGH_CHARS = 4000
HEURISTIC_LOW_CHARS = 280
HEURISTIC_MAX_QUESTION_MARKS = 1

_HEURISTIC_CODE_FENCE = "```"
_HEURISTIC_ENGINEERING_RE = re.compile(
    r"\b(refactor|implement|migrate|debug|deploy|architect|fix the bug)\b",
    re.IGNORECASE,
)
_HEURISTIC_TRACEBACK_RE = re.compile(
    r"Traceback \(most recent call last\)"
    r'|File "[^"]+", line \d+'
    r"|^\s+at .+\(.+:\d+:\d+\)",
    re.MULTILINE,
)


def _as_complexity(value: Complexity | str | None) -> Complexity | None:
    if isinstance(value, Complexity):
        return value
    if isinstance(value, str):
        upper = value.strip().upper()
        if upper in Complexity._value2member_map_:
            return Complexity(upper)
    return None


def heuristic_route(
    user_text: str,
    *,
    prior_route: Complexity | str | None = None,
    history_len: int = 0,
    sticky_high: bool = True,
) -> Complexity | None:
    """Deterministic route for confident turns; ``None`` means "classify".

    Conservative by design: a confidently wrong LOW/HIGH skips the classifier, so
    only unambiguous shapes are gated. Sticky HIGH applies only when the gate is
    otherwise undecided, so a fresh simple question in a long complex session
    still routes LOW.
    """
    text = user_text or ""
    if not text.strip():
        return Complexity.LOW
    if (
        len(text) > HEURISTIC_HIGH_CHARS
        or _HEURISTIC_CODE_FENCE in text
        or _HEURISTIC_ENGINEERING_RE.search(text)
        or _HEURISTIC_TRACEBACK_RE.search(text)
    ):
        return Complexity.HIGH
    if (
        len(text) <= HEURISTIC_LOW_CHARS
        and _HEURISTIC_CODE_FENCE not in text
        and text.count("?") <= HEURISTIC_MAX_QUESTION_MARKS
    ):
        return Complexity.LOW
    if sticky_high and history_len > 0 and _as_complexity(prior_route) is Complexity.HIGH:
        return Complexity.HIGH
    return None


_classifier_cache: dict[tuple[str, str], Agent[Any, str]] = {}


def clear_classifier_cache() -> None:
    _classifier_cache.clear()


def _classifier_agent(
    settings: LatticeSettings, model_id: str, system_prompt: str
) -> Agent[Any, str]:
    # Keyed on stable bytes only: the per-session sticky hint travels on the run
    # (see ``decide_route``) so one cached Agent is shared across sessions.
    key = (model_id, system_prompt)
    cached = _classifier_cache.get(key)
    if cached is not None:
        return cached
    model_obj = build_openai_model(settings, model_id)
    model_settings: dict[str, Any] = {**prompt_cache_settings(settings, model_obj)}
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
        agent = _classifier_agent(settings, model_id, system_prompt)
        cfg = settings.agent.orchestrator
        # Truncate before prepending the marker so ``[route]`` stays intact.
        probe = user_text[: max(0, cfg.classifier_input_chars)]
        result = await with_deadline(
            agent.run(
                f"{ROUTE_MARKER}\n{probe}",
                model_settings=session_routing_settings(settings, session_id) or None,
            ),
            seconds=float(cfg.classifier_timeout_seconds),
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
