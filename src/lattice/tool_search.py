"""In-process BM25 ranking for deferred tool discovery.

The deferred-tool corpus is small and rebuilt on every ``search_tools`` call, so
an exact, pure-Python BM25 needs no index, embedding model, or vector store.
IDF weights rare, discriminating tokens (``schedule``) above ubiquitous ones
(``file``, ``list``), which raw keyword overlap cannot do.

This is the *local* algorithm behind ``tools.search_strategy: bm25``. It is kept
byte-reproducible and side-effect free: no I/O, no model, no async.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any

from pydantic_ai.tools import ToolDefinition

from lattice.text import tokenize
from lattice.tool_names import canonical_name

__all__ = ["bm25_search_fn", "rank_tools"]

# Standard BM25 saturation / length-normalization knobs.
_K1 = 1.2
_B = 0.75
# Name tokens count for more than description tokens.
_NAME_BOOST = 2.0

# Curated ``canonical tool name -> alias tokens`` table. Aliases only add recall;
# they are appended document-side with description weight and never override a
# literal name/description match. Aliases already present in a tool's own name or
# description are dropped before indexing so they cannot inflate TF/dl for free.
_ALIASES: dict[str, tuple[str, ...]] = {
    "media/chart": ("graph", "plot", "diagram", "visualize", "visualization", "dashboard"),
    "media/pdf": ("report", "document", "export", "print"),
    "browser/interact": ("scrape", "navigate", "click", "page", "browser"),
    "browser/snapshot": ("page", "html", "inspect", "screenshot", "browser"),
    "compute/script": ("code", "python", "node", "bash", "sandbox"),
    "schedule/list": ("reminders", "jobs", "upcoming"),
    "schedule/cancel": ("reminder", "job"),
    "schedule/timezone_get": ("tz", "clock", "utc"),
    "schedule/timezone_set": ("tz", "clock", "utc"),
    "sqlite/list": ("database", "tables"),
    "sqlite/execute": ("sql", "write", "ddl", "dml", "database"),
    "sqlite/register": ("database", "attach", "detach"),
    "sqlite/unregister": ("database", "attach", "detach"),
    "sqlite/backup": ("backup", "dump", "database"),
    "memory/update": ("remember", "delete"),
    "memory/forget": ("remember", "delete"),
    "profiles/list": ("profile", "persona"),
    "profiles/remove": ("profile", "persona"),
}


def _split_tokens(name: str, description: str | None) -> tuple[list[str], list[str]]:
    # The corpus carries model-facing wire names (``sqlite__execute``); aliases
    # are keyed by canonical name, so normalize before both tokenizing and lookup.
    canonical = canonical_name(name)
    name_tokens = tokenize(canonical)
    description_tokens = tokenize(description or "")
    known = set(name_tokens) | set(description_tokens)
    alias_tokens = [
        token
        for alias in _ALIASES.get(canonical, ())
        for token in tokenize(alias)
        if token not in known
    ]
    return name_tokens, description_tokens + alias_tokens


def _term_frequencies(name_tokens: list[str], description_tokens: list[str]) -> dict[str, float]:
    """Weighted term frequencies: name tokens boosted over description tokens."""
    tf: dict[str, float] = {}
    for token in description_tokens:
        tf[token] = tf.get(token, 0.0) + 1.0
    for token in name_tokens:
        tf[token] = tf.get(token, 0.0) + _NAME_BOOST
    return tf


def _rank_scored(
    queries: Sequence[str], corpus: Sequence[ToolDefinition]
) -> list[tuple[float, str]]:
    """Score every tool with a positive BM25 score against the queries.

    Returns ``(score, name)`` pairs in descending-score order (tie-broken by name
    for determinism); ``max_results`` trimming is the framework's job. Returns
    ``[]`` for blank queries or an empty corpus.
    """
    query_terms = set(tokenize(" ".join(queries)))
    if not query_terms or not corpus:
        return []

    docs: list[tuple[str, dict[str, float]]] = []
    for tool_def in corpus:
        name_tokens, description_tokens = _split_tokens(tool_def.name, tool_def.description)
        docs.append((tool_def.name, _term_frequencies(name_tokens, description_tokens)))

    n = len(docs)
    avgdl = sum(sum(tf.values()) for _, tf in docs) / n
    if avgdl <= 0:
        return []

    doc_freq: dict[str, int] = {}
    for _, tf in docs:
        for term in tf:
            doc_freq[term] = doc_freq.get(term, 0) + 1

    scored: list[tuple[float, str]] = []
    for name, tf in docs:
        dl = sum(tf.values())
        score = 0.0
        for term in query_terms:
            freq = tf.get(term, 0.0)
            if freq <= 0:
                continue
            df = doc_freq.get(term, 0)
            # Always-positive IDF so a term in most docs is downweighted, not dropped.
            idf = math.log(1.0 + (n - df + 0.5) / (df + 0.5))
            denom = freq + _K1 * (1.0 - _B + _B * dl / avgdl)
            score += idf * freq * (_K1 + 1.0) / denom
        if score > 0:
            scored.append((score, name))

    scored.sort(key=lambda item: (-item[0], item[1]))
    return scored


def rank_tools(queries: Sequence[str], corpus: Sequence[ToolDefinition]) -> list[str]:
    """Names of every positive BM25 match, highest score first (untrimmed)."""
    return [name for _, name in _rank_scored(queries, corpus)]


def _apply_cutoff(scored: Sequence[tuple[float, str]], min_ratio: float) -> list[tuple[float, str]]:
    """Drop matches scoring below ``min_ratio * top_score``.

    ``min_ratio <= 0`` disables trimming. The single best match is always kept so
    a query can never come back empty merely because every match is weak.
    """
    if min_ratio <= 0 or not scored:
        return list(scored)
    threshold = scored[0][0] * min_ratio
    return [item for index, item in enumerate(scored) if index == 0 or item[0] >= threshold]


def bm25_search_fn(
    ctx: Any,
    queries: Sequence[str],
    corpus: Sequence[ToolDefinition],
    *,
    min_ratio: float = 0.0,
) -> list[str]:
    """``ToolSearchFunc`` adapter: BM25 ranking with undiscovered-first ordering.

    ``min_ratio`` trims weak matches relative to the top score; ``0`` disables
    trimming. A stable sort on ``name in discovered`` keeps the BM25 score order
    inside each group, so an already-discovered top match never displaces a fresh
    one when the framework trims to ``max_results``. Any scoring failure degrades
    to ``[]`` rather than failing the turn.
    """
    try:
        scored = _apply_cutoff(_rank_scored(queries, corpus), min_ratio)
    except Exception:  # noqa: BLE001 — a ranking bug must never fail a turn
        return []
    ranked = [name for _, name in scored]
    discovered = getattr(ctx, "discovered_tool_names", None) or set()
    ranked.sort(key=lambda name: name in discovered)
    return ranked
