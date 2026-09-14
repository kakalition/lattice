"""Canonical/wire name registry contract."""

from __future__ import annotations

import re

import pytest

from lattice.tool_names import (
    CORE_TOOL_NAMES,
    DEFAULT_TIERS,
    GROUP_NAMES,
    LEGACY_ALIASES,
    RESERVED_GROUPS,
    RESERVED_LEAVES,
    WIRE_NAME_RE,
    canonical_name,
    group_of,
    leaf_of,
    normalize_name,
    normalize_pattern,
    wire_name,
)

_PROVIDER_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")


@pytest.mark.parametrize("canonical", CORE_TOOL_NAMES)
def test_wire_name_matches_provider_grammar(canonical: str) -> None:
    wire = wire_name(canonical)
    assert _PROVIDER_RE.match(wire), wire
    assert WIRE_NAME_RE.match(wire)


@pytest.mark.parametrize("canonical", CORE_TOOL_NAMES)
def test_canonical_wire_round_trip(canonical: str) -> None:
    assert canonical_name(wire_name(canonical)) == canonical


@pytest.mark.parametrize("canonical", CORE_TOOL_NAMES)
def test_canonical_name_is_idempotent(canonical: str) -> None:
    assert canonical_name(canonical) == canonical
    assert normalize_name(wire_name(canonical)) == canonical


def test_group_and_leaf_split() -> None:
    assert group_of("sqlite/execute") == "sqlite"
    assert leaf_of("sqlite/execute") == "execute"
    assert group_of("execute") == ""
    assert leaf_of("execute") == "execute"


def test_legacy_flat_names_map_to_canonical() -> None:
    assert canonical_name("sqlite_execute") == "sqlite/execute"
    assert canonical_name("read_file") == "files/read"
    assert canonical_name("skill_view") == "skills/view"
    # Every legacy alias target is a real core tool.
    for flat, canonical in LEGACY_ALIASES.items():
        assert flat not in CORE_TOOL_NAMES
        assert canonical in DEFAULT_TIERS


def test_normalize_pattern_maps_legacy_globs() -> None:
    assert normalize_pattern("sqlite_*") == "sqlite/*"
    assert normalize_pattern("web_*") == "web/*"
    assert normalize_pattern("generate_*") == "media/*"
    assert normalize_pattern("session_*") == "memory/*"
    # Exact legacy names normalize to the canonical tool.
    assert normalize_pattern("read_file") == "files/read"
    # Canonical patterns and unknown patterns pass through unchanged.
    assert normalize_pattern("sqlite/*") == "sqlite/*"
    assert normalize_pattern("user/*") == "user/*"


def test_reserved_names() -> None:
    assert set(GROUP_NAMES) <= RESERVED_GROUPS
    assert {"user", "mcp"} <= RESERVED_GROUPS
    assert {"execute", "read", "search_tools"} <= RESERVED_LEAVES
    # A reserved group is never a valid external namespace.
    assert "sqlite" in RESERVED_GROUPS


def test_no_duplicate_core_or_default_tiers() -> None:
    assert len(CORE_TOOL_NAMES) == len(set(CORE_TOOL_NAMES))
    assert set(CORE_TOOL_NAMES) == set(DEFAULT_TIERS)
