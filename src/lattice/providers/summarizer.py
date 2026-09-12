"""Conversation summarizer for context compression (runs on the primary model)."""

from __future__ import annotations

import threading

from pydantic_ai import Agent

from lattice.config import LatticeSettings
from lattice.providers.logging_model import with_llm_logging
from lattice.providers.openai_compat import build_openai_model

_lock = threading.Lock()
_agents: dict[str, Agent] = {}


def _build_agent(settings: LatticeSettings, model_id: str) -> Agent:
    # Reuse the agent across compressions: provider client init is not free, and
    # the summarizer runs on the critical path.
    with _lock:
        existing = _agents.get(model_id)
        if existing is not None:
            return existing
    agent = Agent(
        with_llm_logging(build_openai_model(settings, model_id)),
        system_prompt="You summarize conversation transcripts concisely for long-term context.",
    )
    with _lock:
        _agents[model_id] = agent
    return agent


class Summarizer:
    def __init__(self, settings: LatticeSettings, model_id: str) -> None:
        self.settings = settings
        self.model_id = model_id
        self._agent = _build_agent(settings, model_id)

    async def summarize(self, transcript: str, *, max_words: int = 400) -> str:
        prompt = (
            f"Summarize the following conversation middle in under {max_words} words. "
            "Preserve decisions, facts, tool outcomes, and open tasks.\n\n"
            f"{transcript}"
        )
        result = await self._agent.run(prompt)
        return str(result.output)
