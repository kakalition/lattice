"""Provider error taxonomy."""

from __future__ import annotations

from enum import StrEnum


class FailoverReason(StrEnum):
    RATE_LIMIT = "rate_limit"
    CONTEXT_OVERFLOW = "context_overflow"
    EMPTY_COMPLETION = "empty_completion"
    TRUNCATED_TOOL_JSON = "truncated_tool_json"
    AUTH = "auth"
    TIMEOUT = "timeout"
    TRANSIENT = "transient"
    FATAL = "fatal"


def classify_provider_error(exc: BaseException) -> FailoverReason:
    text = str(exc).lower()
    status = getattr(exc, "status_code", None)
    if status is None:
        status = getattr(exc, "status", None)
    # Typed signals win over substring heuristics.
    if isinstance(exc, TimeoutError) or "timeout" in text or "timed out" in text:
        return FailoverReason.TIMEOUT
    if status == 429 or "429" in text or "rate limit" in text or "rate_limit" in text:
        return FailoverReason.RATE_LIMIT
    if status in {500, 502, 503, 504} or "500" in text or "502" in text or "503" in text:
        return FailoverReason.TRANSIENT
    if status in {401, 403} or "401" in text or "auth" in text or "api key" in text:
        return FailoverReason.AUTH
    # Parenthesized: ``and`` binds tighter than ``or`` — ``token and limit`` must
    # not make every message containing "context" or "maximum" an overflow.
    if "context" in text or "maximum" in text or ("token" in text and "limit" in text):
        return FailoverReason.CONTEXT_OVERFLOW
    if "empty" in text:
        return FailoverReason.EMPTY_COMPLETION
    if "tool_call" in text or "json" in text:
        return FailoverReason.TRUNCATED_TOOL_JSON
    return FailoverReason.FATAL


def recovery_action(reason: FailoverReason) -> str:
    """Map taxonomy → retry / compress / abort."""
    return {
        FailoverReason.RATE_LIMIT: "retry",
        FailoverReason.CONTEXT_OVERFLOW: "compress",
        FailoverReason.EMPTY_COMPLETION: "retry",
        FailoverReason.TRUNCATED_TOOL_JSON: "retry",
        FailoverReason.AUTH: "abort",
        FailoverReason.TIMEOUT: "retry",
        FailoverReason.TRANSIENT: "retry",
        FailoverReason.FATAL: "abort",
    }[reason]
