"""Full Hermes-style context compressor."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from lattice.context.pressure import PressureConfig
from lattice.providers.auxiliary import AuxiliaryClient


@dataclass
class CompressResult:
    messages: list[dict[str, Any]]
    summary: str
    compressed: bool
    used_trim_fallback: bool = False


def _tool_pair_indices(messages: list[dict[str, Any]]) -> set[int]:
    """Indices that must stay with their pairs (assistant tool_calls + tool results)."""
    protected: set[int] = set()
    for i, msg in enumerate(messages):
        if msg.get("role") == "assistant" and msg.get("tool_calls"):
            protected.add(i)
            ids = {tc.get("id") for tc in msg["tool_calls"]}
            for j in range(i + 1, len(messages)):
                if messages[j].get("role") == "tool" and messages[j].get("tool_call_id") in ids:
                    protected.add(j)
        if msg.get("role") == "tool":
            protected.add(i)
    return protected


async def compress(
    messages: list[dict[str, Any]],
    *,
    aux: AuxiliaryClient | None,
    protect_last_n: int = 20,
    pressure: PressureConfig | None = None,
) -> CompressResult:
    pressure = pressure or PressureConfig()
    if len(messages) <= protect_last_n + 2 or not pressure.is_over_pressure(messages):
        return CompressResult(messages=messages, summary="", compressed=False)

    head = messages[:1] if messages and messages[0].get("role") == "system" else []
    start = len(head)
    end = max(start, len(messages) - protect_last_n)
    middle = messages[start:end]
    tail = messages[end:]
    if not middle:
        return CompressResult(messages=messages, summary="", compressed=False)

    # Avoid splitting tool pairs across middle/tail boundary
    pair_idx = _tool_pair_indices(messages)
    while end < len(messages) and end in pair_idx:
        end += 1
        middle = messages[start:end]
        tail = messages[end:]

    transcript = "\n".join(
        f"{m.get('role')}: {m.get('content')}" for m in middle if m.get("content")
    )
    summary = ""
    used_trim = False
    if aux is not None:
        try:
            summary = await aux.summarize(transcript)
        except Exception:
            used_trim = True
            summary = "(trim fallback — aux summarize failed)"
            middle = middle[: max(1, len(middle) // 4)]
            transcript = "\n".join(str(m.get("content")) for m in middle)
            summary = f"Trimmed older context. Kept excerpt:\n{transcript[:1500]}"
    else:
        used_trim = True
        summary = f"Trimmed older context. Excerpt:\n{transcript[:1500]}"

    summary_msg = {
        "role": "system",
        "content": f"[compressed context summary]\n{summary}",
    }
    new_messages = [*head, summary_msg, *tail]
    return CompressResult(
        messages=new_messages,
        summary=summary,
        compressed=True,
        used_trim_fallback=used_trim,
    )
