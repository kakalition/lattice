"""Conversation summarizer for context compression (runs on the primary model)."""

from __future__ import annotations

from pydantic_ai import Agent

from lattice.config import LatticeSettings
from lattice.providers.logging_model import with_llm_logging
from lattice.providers.openai_compat import build_openai_model


class Summarizer:
    def __init__(self, settings: LatticeSettings, model_id: str) -> None:
        self.settings = settings
        self.model_id = model_id
        self._agent = Agent(
            with_llm_logging(build_openai_model(settings, model_id)),
            system_prompt="You summarize conversation transcripts concisely for long-term context.",
        )

    async def summarize(self, transcript: str, *, max_words: int = 400) -> str:
        prompt = (
            f"Summarize the following conversation middle in under {max_words} words. "
            "Preserve decisions, facts, tool outcomes, and open tasks.\n\n"
            f"{transcript}"
        )
        result = await self._agent.run(prompt)
        return str(result.output)
