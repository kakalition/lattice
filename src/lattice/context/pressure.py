"""Context pressure thresholds."""

from __future__ import annotations

from pydantic import BaseModel


class PressureConfig(BaseModel):
    ratio: float = 0.5
    model_context_tokens: int = 128_000
    chars_per_token: float = 4.0

    def threshold_chars(self) -> int:
        return int(self.model_context_tokens * self.ratio * self.chars_per_token)

    def estimate_chars(self, messages: list[dict], *, extra_chars: int = 0) -> int:
        total = extra_chars
        for msg in messages:
            content = msg.get("content")
            if isinstance(content, str):
                total += len(content)
            else:
                total += len(str(content))
            if msg.get("tool_calls"):
                total += len(str(msg["tool_calls"]))
        return total

    def is_over_pressure(self, messages: list[dict], *, extra_chars: int = 0) -> bool:
        return self.estimate_chars(messages, extra_chars=extra_chars) >= self.threshold_chars()
