"""Convert stored session dicts to Pydantic AI message history (cacheable prefix)."""

from __future__ import annotations

from typing import Any

from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    UserPromptPart,
)


def session_dicts_to_history(messages: list[dict[str, Any]]) -> list[ModelMessage]:
    """Keep user/assistant text turns; drop tool pairs (unsafe to replay without schemas)."""
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
    return history
