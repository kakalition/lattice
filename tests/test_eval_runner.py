"""Eval runner: passing and failing corpus rows produce expected status."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from lattice.eval.corpus import CorpusRow, InboundSpec
from lattice.eval.runner import _decision_for, run_eval

BUNDLED = Path(__file__).parent / "eval" / "corpus"


@pytest.mark.asyncio
async def test_runner_reports_pass_and_fail(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    shutil.copy(BUNDLED / "plain.jsonl", corpus / "plain.jsonl")
    (corpus / "rows.jsonl").write_text(
        '{"id":"ok","inbound":{"text":"Say something offline.","channel":"cli",'
        '"profile_id":"default"},'
        '"expect":{"output_contains":["Offline replay works."]},'
        '"cassette":"plain.jsonl"}\n'
        '{"id":"bad","inbound":{"text":"Say something offline.","channel":"cli",'
        '"profile_id":"default"},'
        '"expect":{"output_contains":["definitely-not-present"]},'
        '"cassette":"plain.jsonl"}\n',
        encoding="utf-8",
    )
    report = await run_eval(corpus, home=tmp_path / "home", output=tmp_path / "report.json")
    assert report.passed == 1
    assert report.failed == 1
    bad = next(r for r in report.rows if r.id == "bad")
    assert bad.passed is False
    ok = next(r for r in report.rows if r.id == "ok")
    assert ok.passed is True
    assert (tmp_path / "report.json").is_file()


@pytest.mark.asyncio
async def test_draft_row_without_cassette_is_skipped(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "rows.jsonl").write_text(
        '{"id":"draft","inbound":{"text":"hi"},"expect":{}}\n', encoding="utf-8"
    )
    report = await run_eval(corpus, home=tmp_path / "home")
    assert report.skipped == 1
    assert report.failed == 0


def test_effective_turns_includes_inbound_plus_extra() -> None:
    row = CorpusRow(
        id="multi",
        inbound=InboundSpec(text="one"),
        turns=[InboundSpec(text="two"), InboundSpec(text="three")],
    )
    assert [spec.text for spec in row.effective_turns()] == ["one", "two", "three"]


def test_hitl_decision_maps_from_row() -> None:
    from lattice.hitl import ApprovalDecision

    assert _decision_for(CorpusRow(id="a", inbound=InboundSpec(text="x"))) is (
        ApprovalDecision.DENY
    )
    approve = CorpusRow(id="b", inbound=InboundSpec(text="x"), hitl_decision="approve")
    assert _decision_for(approve) is ApprovalDecision.APPROVE


@pytest.mark.asyncio
async def test_latency_and_cost_assertions(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    shutil.copy(BUNDLED / "plain.jsonl", corpus / "plain.jsonl")
    (corpus / "rows.jsonl").write_text(
        '{"id":"slow","inbound":{"text":"Say something offline.","channel":"cli",'
        '"profile_id":"default"},"expect":{"max_duration_ms":-1,"max_ttft_ms":-1},'
        '"cassette":"plain.jsonl"}\n',
        encoding="utf-8",
    )
    report = await run_eval(corpus, home=tmp_path / "home")
    row = report.rows[0]
    assert report.failed == 1
    names = {a.name for a in row.assertions if not a.passed}
    assert {"max_duration_ms", "max_ttft_ms"} <= names


@pytest.mark.asyncio
async def test_multi_turn_row_reuses_session(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    shutil.copy(BUNDLED / "read_reread.jsonl", corpus / "read_reread.jsonl")
    (corpus / "rows.jsonl").write_text(
        '{"id":"read-reread","inbound":{"text":"Read note.txt and tell me what it says.",'
        '"channel":"cli","profile_id":"default"},'
        '"turns":[{"text":"What does note.txt say now?","channel":"cli",'
        '"profile_id":"default"}],"files":{"note.txt":"hello world"},'
        '"expect":{"tools":["files/read"],"output_contains":["hello world"],'
        '"no_prompt_drift":true,"max_requests":4},"cassette":"read_reread.jsonl"}\n',
        encoding="utf-8",
    )
    report = await run_eval(corpus, home=tmp_path / "home")
    row = report.rows[0]
    assert row.passed, row.assertions
    assert row.prompt_drift is False
    assert row.requests == 4
    assert row.tools == ["files/read", "files/read"]


@pytest.mark.asyncio
async def test_stale_turn_record_is_removed_between_runs(tmp_path: Path) -> None:
    from lattice.setup import init_home

    corpus = tmp_path / "corpus"
    corpus.mkdir()
    shutil.copy(BUNDLED / "plain.jsonl", corpus / "plain.jsonl")
    (corpus / "rows.jsonl").write_text(
        '{"id":"ok","inbound":{"text":"Say something offline.","channel":"cli",'
        '"profile_id":"default"},'
        '"expect":{},"cassette":"plain.jsonl"}\n',
        encoding="utf-8",
    )
    home = tmp_path / "home"
    stale_home = home / "evals" / "tmp" / "ok"
    init_home(stale_home)
    stale_file = stale_home / "logs" / "turns.jsonl"
    stale_file.parent.mkdir(parents=True, exist_ok=True)
    stale_file.write_text('{"turn_id":"STALE","usage":{"requests":999}}\n', encoding="utf-8")

    report = await run_eval(corpus, home=home)
    assert report.rows[0].passed
    text = stale_file.read_text(encoding="utf-8")
    assert "STALE" not in text
    assert report.rows[0].requests != 999


@pytest.mark.asyncio
async def test_request_budget_regression_fails(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    shutil.copy(BUNDLED / "plain.jsonl", corpus / "plain.jsonl")
    (corpus / "rows.jsonl").write_text(
        '{"id":"over-budget","inbound":{"text":"Say something offline.","channel":"cli",'
        '"profile_id":"default"},"expect":{"max_requests":0},"cassette":"plain.jsonl"}\n',
        encoding="utf-8",
    )
    report = await run_eval(corpus, home=tmp_path / "home")
    assert report.failed == 1
    row = report.rows[0]
    assert row.requests >= 1
    assert any(a.name == "max_requests" and not a.passed for a in row.assertions)
