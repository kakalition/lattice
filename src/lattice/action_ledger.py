"""Compact action ledger: what the harness did, not the raw tool transcript.

Full tool-pair replay would bloat context and risk toolset/schema drift. Instead
we persist one short record per tool call and render it into the volatile tail,
so the model stops re-discovering the same files and databases each turn.
Privacy: every string is clipped and secret-looking values are redacted; file
bodies and full tool results are never stored.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field
from pydantic_ai.messages import ModelMessage, ToolCallPart, ToolReturnPart

_TARGET_KEYS = (
    "path",
    "file",
    "filepath",
    "filename",
    "name",
    "target",
    "query",
    "sql",
    "command",
    "url",
    "pattern",
    "id",
    "reminder",
    "prompt",
)
_ARTIFACT_TOOLS = frozenset({"write_file", "edit_file", "generate_chart", "generate_pdf"})
_SECRET_RE = re.compile(r"(?i)\b(api[_-]?key|secret|token|password|passwd|bearer)\b\s*[:=]?\s*\S+")
_MAX_TARGET = 120
_MAX_OUTCOME = 120
_MAX_ARTIFACT = 160


class ActionRecord(BaseModel):
    tool: str
    target: str = ""
    ok: bool = True
    outcome: str = ""
    artifacts: list[str] = Field(default_factory=list)


def _clip(value: Any, limit: int) -> str:
    text = "" if value is None else str(value)
    text = _SECRET_RE.sub(lambda m: f"{m.group(1)}=***", text)
    text = " ".join(text.split())
    if len(text) > limit:
        text = text[:limit] + "…"
    return text


def _args_dict(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            loaded = json.loads(raw)
        except (ValueError, TypeError):
            return {}
        if isinstance(loaded, dict):
            return loaded
    return {}


def _target_from_args(raw: Any) -> str:
    args = _args_dict(raw)
    for key in _TARGET_KEYS:
        value = args.get(key)
        if value not in (None, "", {}):
            return _clip(value, _MAX_TARGET)
    if args:
        return _clip(args, _MAX_TARGET)
    return _clip(raw, _MAX_TARGET)


def _artifacts_from_args(tool: str, raw: Any) -> list[str]:
    if tool not in _ARTIFACT_TOOLS:
        return []
    args = _args_dict(raw)
    out: list[str] = []
    for key in ("path", "file", "filename", "output", "out"):
        value = args.get(key)
        if value:
            out.append(_clip(value, _MAX_ARTIFACT))
    return out


def _result_ok(content: Any, part: ToolReturnPart) -> bool:
    declared = getattr(part, "outcome", None)
    if declared == "failed":
        return False
    low = str(content).lstrip().lower()
    return not (low.startswith("error:") or low.startswith("denied"))


def actions_from_messages(
    messages: list[ModelMessage],
    *,
    media: list[Path] | None = None,
    max_actions: int = 40,
) -> list[ActionRecord]:
    """Pair ToolCall/ToolReturn parts into bounded action records."""
    calls: dict[str, ToolCallPart] = {}
    records: list[ActionRecord] = []
    for message in messages:
        for part in getattr(message, "parts", None) or []:
            if isinstance(part, ToolCallPart):
                call_id = part.tool_call_id or part.id or ""
                if call_id:
                    calls[call_id] = part
            elif isinstance(part, ToolReturnPart):
                call_id = part.tool_call_id or ""
                call = calls.pop(call_id, None)
                tool = part.tool_name or (call.tool_name if call else "?")
                raw_args = call.args if call is not None else {}
                records.append(
                    ActionRecord(
                        tool=tool,
                        target=_target_from_args(raw_args),
                        ok=_result_ok(part.content, part),
                        outcome=_clip(part.content, _MAX_OUTCOME),
                        artifacts=_artifacts_from_args(tool, raw_args),
                    )
                )
    if media and records:
        existing = {artifact for record in records for artifact in record.artifacts}
        extra = [_clip(str(p), _MAX_ARTIFACT) for p in media if str(p) not in existing]
        records[-1].artifacts.extend(extra)
    return records[-max_actions:]
