"""Eval runner: passing and failing corpus rows produce expected status."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from lattice.eval.runner import run_eval

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
