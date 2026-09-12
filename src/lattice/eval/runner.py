"""Offline eval runner: replay a corpus through production ``run_turn``."""

from __future__ import annotations

import logging
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from lattice.config import LatticeSettings
from lattice.eval.cassette import ReplayModel, build_replay_model
from lattice.eval.corpus import CorpusRow, load_corpus
from lattice.events import NullTurnEvents
from lattice.hitl import ApprovalDecision, ApprovalRequest, AutoApproveHitl
from lattice.memory import InMemoryMemory
from lattice.models import Inbound
from lattice.session import SessionStore
from lattice.setup import init_home
from lattice.turn import run_turn
from lattice.turn_record import turn_records_path

logger = logging.getLogger("lattice.eval.runner")


class AssertionResult(BaseModel):
    name: str
    passed: bool
    detail: str = ""


class RowReport(BaseModel):
    id: str
    passed: bool
    skipped: bool = False
    output: str = ""
    tools: list[str] = Field(default_factory=list)
    forbidden_hits: list[str] = Field(default_factory=list)
    tool_calls: int = 0
    tools_offered: int = 0
    hitl_prompts: int = 0
    retries: int = 0
    ttft_ms: int | None = None
    duration_ms: int = 0
    requests: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost: float | None = None
    cache_hit_ratio: float = 0.0
    prompt_drift: bool = False
    assertions: list[AssertionResult] = Field(default_factory=list)


class EvalReport(BaseModel):
    started_at: str
    corpus_dir: str
    rows: list[RowReport] = Field(default_factory=list)
    passed: int = 0
    failed: int = 0
    skipped: int = 0


class _CollectingEvents:
    """Captures tool starts for assertions; forwards everything else."""

    def __init__(self) -> None:
        self.tools: list[str] = []
        self.inner = NullTurnEvents()

    async def on_status(self, message: str) -> None:
        await self.inner.on_status(message)

    async def on_stream_delta(self, text: str) -> None:
        await self.inner.on_stream_delta(text)

    async def on_tool_start(self, name: str, args: dict[str, Any]) -> None:
        self.tools.append(name)
        await self.inner.on_tool_start(name, args)

    async def on_tool_end(self, name: str, result: str) -> None:
        await self.inner.on_tool_end(name, result)


class CountingHitl(AutoApproveHitl):
    def __init__(self, decision: ApprovalDecision = ApprovalDecision.DENY) -> None:
        super().__init__(approve_all=False)
        self.decision = decision
        self.prompts = 0

    async def approve(self, req: ApprovalRequest) -> ApprovalDecision:
        self.prompts += 1
        return self.decision


def _turn_records(home: Path) -> list[dict[str, Any]]:
    path = turn_records_path(home)
    if not path.is_file():
        return []
    import json

    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            parsed = json.loads(line)
        except ValueError:
            continue
        if isinstance(parsed, dict):
            records.append(parsed)
    return records


def _last_turn_record(home: Path) -> dict[str, Any]:
    records = _turn_records(home)
    return records[-1] if records else {}


def _decision_for(row: CorpusRow) -> ApprovalDecision:
    if row.hitl_decision == "approve":
        return ApprovalDecision.APPROVE
    return ApprovalDecision.DENY


async def run_row(row: CorpusRow, *, corpus_dir: Path, run_home: Path) -> RowReport:
    report = RowReport(id=row.id, passed=False)
    cassette = (corpus_dir / row.cassette) if row.cassette else None
    if cassette is None or not cassette.is_file():
        report.skipped = True
        report.passed = True
        report.assertions.append(
            AssertionResult(name="cassette", passed=True, detail="draft row (no cassette)")
        )
        return report

    # Hermetic: a reused run home would leak a previous run's state.db,
    # turns.jsonl, workspace, and read caches into this row's metrics.
    if run_home.exists():
        shutil.rmtree(run_home, ignore_errors=True)
    init_home(run_home)
    settings = LatticeSettings(home=run_home)
    settings.agent.workspace = run_home / "workspace"
    settings.agent.workspace.mkdir(parents=True, exist_ok=True)
    # Local-only memory: no mem0/network during eval.
    settings.memory.self_check = False
    for rel, content in row.files.items():
        target = settings.agent.workspace / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    replay: ReplayModel = build_replay_model(cassette)
    collector = _CollectingEvents()
    hitl = CountingHitl(decision=_decision_for(row))
    store = SessionStore(run_home / "state.db")

    outputs: list[str] = []
    session_id: str | None = None
    before = 0
    requests = input_tokens = output_tokens = retries = tools_offered = 0
    cache_read = 0
    duration_ms = 0
    cost: float | None = None
    ttft_ms: int | None = None

    for spec in row.effective_turns():
        outbound = await run_turn(
            Inbound(
                text=spec.text,
                profile_id=spec.profile_id,
                channel=spec.channel,
                user_id="eval",
                session_id=session_id,
            ),
            settings=settings,
            hitl=hitl,
            events=collector,
            session_store=store,
            model=replay,
            stream=False,
            memory=InMemoryMemory("eval"),
        )
        session_id = outbound.session_id
        outputs.append(outbound.text)
        records = _turn_records(run_home)
        for record in records[before:]:
            usage = record.get("usage") or {}
            requests += int(usage.get("requests") or 0)
            input_tokens += int(usage.get("input_tokens") or 0)
            output_tokens += int(usage.get("output_tokens") or 0)
            cache_read += int(usage.get("cache_read_tokens") or 0)
            retries += int(record.get("retry_count") or 0)
            tools_offered = max(tools_offered, int(record.get("tools_offered") or 0))
            duration_ms += int(record.get("duration_ms") or 0)
            if record.get("ttft_ms") is not None:
                ttft_ms = max(ttft_ms or 0, int(record["ttft_ms"]))
            if usage.get("cost") is not None:
                cost = (cost or 0.0) + float(usage["cost"])
        before = len(records)

    report.output = "\n".join(outputs)
    report.tools = collector.tools
    report.tool_calls = len(collector.tools)
    report.tools_offered = tools_offered
    report.hitl_prompts = hitl.prompts
    report.retries = retries
    report.ttft_ms = ttft_ms
    report.duration_ms = duration_ms
    report.requests = requests
    report.input_tokens = input_tokens
    report.output_tokens = output_tokens
    report.cost = cost
    report.cache_hit_ratio = (cache_read / input_tokens) if input_tokens else 0.0
    report.prompt_drift = replay.prompt_drift

    def check(name: str, ok: bool, detail: str = "") -> None:
        report.assertions.append(AssertionResult(name=name, passed=ok, detail=detail))

    observed = set(collector.tools)
    missing = [t for t in row.expect.tools if t not in observed]
    check("tools", not missing, f"missing: {missing}" if missing else "all present")

    forbidden = [t for t in row.expect.forbidden_tools if t in observed]
    report.forbidden_hits = forbidden
    check(
        "forbidden_tools",
        not forbidden,
        f"hit: {forbidden}" if forbidden else "none",
    )

    if row.expect.output_contains:
        missing_text = [
            snippet for snippet in row.expect.output_contains if snippet not in report.output
        ]
        check(
            "output_contains",
            not missing_text,
            f"missing: {missing_text}" if missing_text else "all present",
        )
    if row.expect.max_tool_calls is not None:
        check(
            "max_tool_calls",
            report.tool_calls <= row.expect.max_tool_calls,
            f"tool_calls={report.tool_calls}",
        )
    if row.expect.max_requests is not None:
        check(
            "max_requests",
            report.requests <= row.expect.max_requests,
            f"requests={report.requests}",
        )
    if row.expect.no_prompt_drift:
        check(
            "no_prompt_drift",
            not report.prompt_drift,
            f"drift={report.prompt_drift}",
        )
    if row.expect.max_duration_ms is not None:
        check(
            "max_duration_ms",
            report.duration_ms <= row.expect.max_duration_ms,
            f"duration_ms={report.duration_ms}",
        )
    if row.expect.max_cost is not None:
        check(
            "max_cost",
            (report.cost or 0.0) <= row.expect.max_cost,
            f"cost={report.cost}",
        )
    if row.expect.max_ttft_ms is not None:
        check(
            "max_ttft_ms",
            report.ttft_ms is not None and report.ttft_ms <= row.expect.max_ttft_ms,
            f"ttft_ms={report.ttft_ms}",
        )

    report.passed = all(a.passed for a in report.assertions)
    return report


async def run_eval(
    corpus_dir: Path,
    *,
    home: Path,
    output: Path | None = None,
) -> EvalReport:
    rows = load_corpus(corpus_dir)
    report = EvalReport(
        started_at=datetime.now(UTC).isoformat(),
        corpus_dir=str(corpus_dir),
    )
    for row in rows:
        run_home = home / "evals" / "tmp" / row.id
        row_report = await run_row(row, corpus_dir=corpus_dir, run_home=run_home)
        report.rows.append(row_report)
        if row_report.skipped:
            report.skipped += 1
        elif row_report.passed:
            report.passed += 1
        else:
            report.failed += 1

    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(report.model_dump_json(indent=2), encoding="utf-8")
    return report
