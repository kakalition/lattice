"""Pure BM25 tool-ranking tests (offline, deterministic)."""

from __future__ import annotations

from types import SimpleNamespace

from pydantic_ai.tools import ToolDefinition

from lattice.tool_search import _apply_cutoff, bm25_search_fn, rank_tools


def _tool(name: str, description: str) -> ToolDefinition:
    return ToolDefinition(name=name, description=description)


CHART = _tool("generate_chart", "Render a chart or graph from tabular data.")
SHELL = _tool("shell", "Run a shell command in the workspace.")
SQLITE = _tool("sqlite_execute", "Execute a write statement against a sqlite database.")
SCHEDULE = _tool("schedule_cancel", "Cancel a scheduled reminder job.")
MEMORY = _tool("memory_forget", "Forget a stored memory entry.")


def test_query_ranks_name_match_first() -> None:
    ranked = rank_tools(["chart"], [CHART, SHELL, SQLITE])
    assert ranked[0] == "generate_chart"
    assert "shell" not in ranked


def test_name_tokenization_splits_snake_case() -> None:
    # ``generate_chart`` tokenizes to {generate, chart}; the query ``chart`` matches.
    assert rank_tools(["chart"], [CHART]) == ["generate_chart"]
    # A camelCase name splits the same way.
    camel = _tool("generateChart", "unrelated words")
    assert rank_tools(["chart"], [camel]) == ["generateChart"]


def test_rare_term_outweighs_ubiquitous_term() -> None:
    corpus = [_tool(f"file_tool_{i}", "file file file file") for i in range(5)] + [
        _tool("quantum_engine", "quantum entanglement runner")
    ]
    # ``file`` appears in most documents, ``quantum`` in one; IDF must favor the rare term.
    ranked = rank_tools(["file quantum"], corpus)
    assert ranked[0] == "quantum_engine"


def test_plural_normalization_matches_singular() -> None:
    assert rank_tools(["charts"], [CHART]) == ["generate_chart"]
    assert rank_tools(["reminders"], [SCHEDULE]) == ["schedule_cancel"]


def test_plural_normalization_does_not_over_collapse() -> None:
    from lattice.text import stem

    assert stem("class") == "class"
    assert stem("classes") == "classe"  # stripped s, not collapsed to ``class``


def test_alias_terms_extend_recall() -> None:
    # Description deliberately omits "graph"; only the alias table supplies it, so
    # a literal-match tool is the control that must not appear.
    chart = _tool("generate_chart", "Render visual output from tabular data.")
    other = _tool("unrelated", "Render visual output from tabular data.")
    assert rank_tools(["graph"], [chart, other]) == ["generate_chart"]


def test_alias_already_in_description_is_not_duplicated() -> None:
    # "graph" is already literal in CHART's description; the alias must be dropped
    # rather than double-counted, leaving a single deterministic match.
    assert rank_tools(["graph"], [CHART]) == ["generate_chart"]


def test_blank_and_unknown_queries_return_empty() -> None:
    corpus = [CHART, SHELL]
    assert rank_tools([], corpus) == []
    assert rank_tools(["", "   "], corpus) == []
    assert rank_tools(["xylophone"], corpus) == []


def test_ranking_is_deterministic() -> None:
    corpus = [CHART, SHELL, SQLITE, SCHEDULE, MEMORY]
    first = rank_tools(["run sqlite execute"], corpus)
    assert first
    for _ in range(5):
        assert rank_tools(["run sqlite execute"], corpus) == first


def test_returns_all_positive_matches_without_trimming() -> None:
    corpus = [_tool(f"tool_{i}", "shared keyword") for i in range(15)]
    ranked = rank_tools(["shared"], corpus)
    assert len(ranked) == 15, "callable must not pre-trim; the framework applies max_results"


def test_undiscovered_first_ordering() -> None:
    # ``shell`` would score higher only if it matched; construct a tie scenario
    # where both match and one is already discovered.
    both = [_tool("alpha_chart", "chart"), _tool("beta_chart", "chart")]
    ctx = SimpleNamespace(discovered_tool_names={"alpha_chart"})
    ordered = bm25_search_fn(ctx, ["chart"], both)
    assert set(ordered) == {"alpha_chart", "beta_chart"}
    assert ordered[0] == "beta_chart", "already-discovered tool must not rank first"


def test_apply_cutoff_relative_ratio() -> None:
    scored = [(10.0, "a"), (4.0, "b"), (1.0, "c")]
    # ``0`` (and negative) disables trimming.
    assert _apply_cutoff(scored, 0.0) == scored
    assert _apply_cutoff(scored, -1.0) == scored
    # 0.4 keeps matches at/above 40% of the top score.
    assert _apply_cutoff(scored, 0.4) == [(10.0, "a"), (4.0, "b")]
    assert _apply_cutoff(scored, 0.5) == [(10.0, "a")]


def test_apply_cutoff_always_keeps_top_match() -> None:
    scored = [(1.0, "only"), (0.001, "weak")]
    assert _apply_cutoff(scored, 1.0) == [(1.0, "only")]
    assert _apply_cutoff([], 0.9) == []


def test_min_ratio_trims_weak_matches() -> None:
    # The long ``pad`` description dilutes the second match's BM25 score; the
    # short match is the clear top result.
    strong = _tool("alpha", "quokka")
    weak = _tool("beta", "quokka " + "pad " * 50)
    ctx = SimpleNamespace(discovered_tool_names=set())

    assert bm25_search_fn(ctx, ["quokka"], [strong, weak], min_ratio=0.0) == ["alpha", "beta"]
    assert bm25_search_fn(ctx, ["quokka"], [strong, weak], min_ratio=0.6) == ["alpha"]
    # A lone weak match is still returned: the top entry is always kept.
    assert bm25_search_fn(ctx, ["quokka"], [weak], min_ratio=0.6) == ["beta"]


def test_ranking_failure_degrades_to_empty(monkeypatch) -> None:
    def boom(*args, **kwargs):
        raise RuntimeError("scorer bug")

    monkeypatch.setattr("lattice.tool_search._rank_scored", boom)
    ctx = SimpleNamespace(discovered_tool_names=set())
    assert bm25_search_fn(ctx, ["anything"], [CHART]) == []
