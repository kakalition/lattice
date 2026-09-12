"""Context pressure thresholds.

The estimator is char-based, but when a previous request reported its actual
``input_tokens`` we calibrate tokens-per-char from that observation instead of
assuming a fixed ratio. This keeps the compaction trigger honest for providers
whose tokenizer diverges from the 4-chars/token default.
"""

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

    def estimate_tokens(
        self,
        messages: list[dict],
        *,
        extra_chars: int = 0,
        observed_tokens: int = 0,
        observed_chars: int = 0,
    ) -> float:
        chars = self.estimate_chars(messages, extra_chars=extra_chars)
        if observed_tokens > 0 and observed_chars > 0:
            return chars * (observed_tokens / observed_chars)
        return chars / self.chars_per_token

    def is_over_pressure(
        self,
        messages: list[dict],
        *,
        extra_chars: int = 0,
        observed_tokens: int = 0,
        observed_chars: int = 0,
    ) -> bool:
        estimated = self.estimate_tokens(
            messages,
            extra_chars=extra_chars,
            observed_tokens=observed_tokens,
            observed_chars=observed_chars,
        )
        return estimated >= self.model_context_tokens * self.ratio
