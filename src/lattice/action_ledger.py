"""Compact action ledger: what the harness did, not the raw tool transcript.

Full tool-pair replay would bloat context and risk toolset/schema drift. Instead
we persist one short record per tool call and render it into the volatile tail,
so the model stops re-discovering the same files and databases each turn.
Privacy: every string is clipped and secret-looking values are redacted; file
bodies and full tool results are never stored.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field
from pydantic_ai.messages import ModelMessage, ToolCallPart, ToolReturnPart

from lattice.deps import result_failed
from lattice.tool_names import normalize_name

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
_ARTIFACT_TOOLS = frozenset({"files/write", "files/edit", "media/chart", "media/pdf"})
# Tools whose result is worth a bounded snippet in the cross-turn ledger.
_EVIDENCE_TOOLS = frozenset({"files/read", "sqlite/query", "sqlite/schema", "web/fetch"})
_SECRET_RE = re.compile(r"(?i)\b(api[_-]?key|secret|token|password|passwd|bearer)\b\s*[:=]?\s*\S+")
_MAX_TARGET = 120
_MAX_OUTCOME = 120
_MAX_ARTIFACT = 160
_MAX_EVIDENCE = 600


class ActionRecord(BaseModel):
    tool: str
    target: str = ""
    ok: bool = True
    outcome: str = ""
    artifacts: list[str] = Field(default_factory=list)
    # Bounded snippet/hash of a read/query result, so the model need not re-run
    # the same discovery next turn. Never a full file/result body.
    evidence: str = ""


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


def _evidence_from_result(tool: str, target: str, content: Any) -> str:
    """Bounded, redacted evidence for a read/query result."""
    if tool not in _EVIDENCE_TOOLS:
        return ""
    text = content if isinstance(content, str) else str(content)
    digest = hashlib.sha256(text.encode("utf-8", "ignore")).hexdigest()[:12]
    head = _clip(text, _MAX_EVIDENCE)
    prefix = f"{target} sha={digest}" if target else f"sha={digest}"
    return f"{prefix} {head}".strip()


def _result_ok(content: Any, part: ToolReturnPart) -> bool:
    declared = getattr(part, "outcome", None)
    if declared == "failed":
        return False
    return not result_failed(str(content))


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
                tool = normalize_name(part.tool_name or (call.tool_name if call else "?"))
                raw_args = call.args if call is not None else {}
                target = _target_from_args(raw_args)
                records.append(
                    ActionRecord(
                        tool=tool,
                        target=target,
                        ok=_result_ok(part.content, part),
                        outcome=_clip(part.content, _MAX_OUTCOME),
                        artifacts=_artifacts_from_args(tool, raw_args),
                        evidence=_evidence_from_result(tool, target, part.content),
                    )
                )
    if media and records:
        existing = {artifact for record in records for artifact in record.artifacts}
        extra = [_clip(str(p), _MAX_ARTIFACT) for p in media if str(p) not in existing]
        records[-1].artifacts.extend(extra)
    return records[-max_actions:]


def actions_from_tool_trace(
    tools: list[Any],
    *,
    media: list[Path] | None = None,
    max_actions: int = 40,
) -> list[ActionRecord]:
    """Synthesize records from turn tool events when the run never returned.

    On timeout/cancel/budget/post-tool provider error ``run_messages`` is empty,
    but the tools already ran and may have had side effects the next turn must
    know about. ``tools`` are ``turn_record.ToolRecord``-shaped (name + ok +
    clipped args), so the record names the operand in flight.
    """
    records: list[ActionRecord] = []
    for t in tools:
        tool = normalize_name(str(getattr(t, "name", "?")))
        raw_args = getattr(t, "args", None) or {}
        target = _target_from_args(raw_args) if raw_args else ""
        records.append(
            ActionRecord(
                tool=tool,
                target=target,
                ok=bool(getattr(t, "ok", True)),
                artifacts=_artifacts_from_args(tool, raw_args),
            )
        )
    records = records[-max_actions:]
    if media and records:
        records[-1].artifacts.extend(_clip(str(p), _MAX_ARTIFACT) for p in media)
    return records
