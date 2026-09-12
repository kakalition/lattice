"""Convert stored session dicts to Pydantic AI message history (cacheable prefix)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic_ai.messages import (
    CachePoint,
    ModelMessage,
    ModelRequest,
    ModelResponse,
    SystemPromptPart,
    TextContent,
    TextPart,
    UserPromptPart,
)


def _attach_cache_point(history: list[ModelMessage], ttl: Literal["5m", "1h"]) -> None:
    """Append a `CachePoint` to the last replayable user prompt.

    The boundary lands on the newest user message, an identity-anchored position
    rather than the moving tail: ``ModelResponse`` parts cannot carry a cache
    marker, so the tail (usually the previous assistant reply) is not a valid
    breakpoint. Skipped when the history has no user request.
    """
    for message in reversed(history):
        if not isinstance(message, ModelRequest):
            continue
        for part in message.parts:
            if not isinstance(part, UserPromptPart):
                continue
            content = part.content
            if isinstance(content, str):
                part.content = [TextContent(content=content), CachePoint(ttl=ttl)]
            else:
                part.content = [*content, CachePoint(ttl=ttl)]
            return


def session_dicts_to_history(
    messages: list[dict[str, Any]],
    *,
    cache_boundary: bool = False,
    cache_ttl: Literal["5m", "1h"] = "5m",
) -> list[ModelMessage]:
    """Keep user/assistant/system text turns; drop tool pairs (unsafe without schemas).

    ``system`` messages carry the compression summary and must round-trip so the
    summary survives replay instead of being silently discarded.
    """
    history: list[ModelMessage] = []
    for msg in messages:
        role = msg.get("role")
        content = msg.get("content")
        if not isinstance(content, str) or not content.strip():
            continue
        if role == "user":
            history.append(ModelRequest(parts=[UserPromptPart(content=content)]))
        elif role == "assistant":
            history.append(ModelResponse(parts=[TextPart(content=content)]))
        elif role == "system":
            history.append(ModelRequest(parts=[SystemPromptPart(content=content)]))
    if cache_boundary and history:
        _attach_cache_point(history, cache_ttl)
    return history
