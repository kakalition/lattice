"""RunUsage → plain-dict conversion for persistence and logging."""

from __future__ import annotations

from typing import Any

from pydantic_ai.usage import RunUsage


def usage_to_dict(usage: RunUsage | None, *, model: str | None = None) -> dict[str, Any]:
    """Flatten run usage into a JSON-serializable summary.

    ``cache_hit_ratio`` is reported so prompt-cache effectiveness is visible in
    the session payload and the turn log.
    """
    data: dict[str, Any] = {}
    if model:
        data["model"] = model
    if usage is None:
        return data
    data.update(
        {
            "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens,
            "cache_read_tokens": usage.cache_read_tokens,
            "cache_write_tokens": usage.cache_write_tokens,
            "cache_hit_ratio": usage.cache_hit_ratio,
            "requests": usage.requests,
        }
    )
    if usage.cost is not None:
        data["cost"] = float(usage.cost)
    return data
