"""Deterministic, dependency-free text normalization for local search.

Shared by the BM25 tool ranker (``lattice.tool_search``) and the memory
backend's lexical index, so both tokenize identically. Keeping it here avoids a
second, subtly different tokenizer drifting from the first.
"""

from __future__ import annotations

import re

_TOKEN_RE = re.compile(r"[a-z0-9]+")
_CAMEL_BOUNDARY_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_MIN_TOKEN_LEN = 2


def stem(token: str) -> str:
    """Light plural normalization; ``ss`` words (``class``) stay intact."""
    if len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    return token


def tokenize(text: str) -> list[str]:
    """Lowercase, split camelCase, drop short tokens, apply light plural stemming."""
    spaced = _CAMEL_BOUNDARY_RE.sub(" ", text).lower()
    return [stem(token) for token in _TOKEN_RE.findall(spaced) if len(token) >= _MIN_TOKEN_LEN]


def normalize(text: str) -> str:
    """Space-joined normalized tokens, used for BM25 indexing and queries alike."""
    return " ".join(tokenize(text))
