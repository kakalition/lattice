"""Full context compressor."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from lattice.context.pressure import PressureConfig
from lattice.providers.summarizer import Summarizer

SUMMARY_MARKER = "[compressed context summary]"


class CompressResult(BaseModel):
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


def _is_summary(msg: dict[str, Any]) -> bool:
    return str(msg.get("content") or "").startswith(SUMMARY_MARKER)


def _render_transcript(messages: list[dict[str, Any]]) -> str:
    """Include tool names + clipped args so the summary keeps what was done."""
    lines: list[str] = []
    for msg in messages:
        role = str(msg.get("role") or "")
        content = msg.get("content")
        calls = msg.get("tool_calls")
        if calls:
            for call in calls:
                fn = (call or {}).get("function") or {}
                name = fn.get("name") or (call or {}).get("name") or "?"
                args = str(fn.get("arguments") or "")
                lines.append(f"assistant tool_call {name}: {args[:200]}")
        if isinstance(content, str) and content.strip():
            lines.append(f"{role}: {content[:2000]}")
    return "\n".join(lines)


async def compress(
    messages: list[dict[str, Any]],
    *,
    aux: Summarizer | None,
    protect_last_n: int = 20,
    pressure: PressureConfig | None = None,
    force: bool = False,
) -> CompressResult:
    pressure = pressure or PressureConfig()
    if len(messages) <= protect_last_n + 2 or (
        not force and not pressure.is_over_pressure(messages)
    ):
        return CompressResult(messages=messages, summary="", compressed=False)

    # A previous compression summary must not accumulate: fold it into the text
    # being summarized and replace it, so exactly one summary survives.
    head: list[dict[str, Any]] = []
    prior_summary = ""
    if messages and _is_summary(messages[0]):
        prior_summary = str(messages[0].get("content") or "")
    elif messages and messages[0].get("role") == "system":
        head = messages[:1]
    start = 1 if (prior_summary or head) else 0
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

    transcript = _render_transcript(middle)
    if prior_summary:
        transcript = f"{prior_summary}\n{transcript}"

    summary = ""
    used_trim = False
    if aux is not None:
        try:
            summary = await aux.summarize(transcript)
        except Exception:
            used_trim = True
            summary = _trim_fallback(middle)
    else:
        used_trim = True
        summary = _trim_fallback(middle)

    summary_msg = {
        "role": "summary",
        "content": f"{SUMMARY_MARKER}\n{summary}",
    }
    new_messages = [*head, summary_msg, *tail]
    return CompressResult(
        messages=new_messages,
        summary=summary,
        compressed=True,
        used_trim_fallback=used_trim,
    )


def _trim_fallback(middle: list[dict[str, Any]]) -> str:
    """Keep the newest quarter — recent context matters more than the oldest."""
    keep = max(1, len(middle) // 4)
    kept = middle[-keep:]
    dropped = len(middle) - len(kept)
    excerpt = _render_transcript(kept)[:1500]
    return (
        f"Trimmed older context ({dropped} message(s) dropped); kept the most recent "
        f"excerpt:\n{excerpt}"
    )
