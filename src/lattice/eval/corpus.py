"""Eval corpus schema plus a miner for real logs.

A corpus row is a single offline scenario. Mined rows are *drafts*: they carry
the observed tool sequence but no invented output assertions, so they seed the
corpus without pretending to know what the right answer was.
"""

from __future__ import annotations

import json
import logging
import re
from collections import OrderedDict
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger("lattice.eval.corpus")

_LOG_LINE_RE = re.compile(r"turn=([0-9a-fA-F]+) (.*)")


class InboundSpec(BaseModel):
    text: str
    channel: str = "cli"
    profile_id: str = "default"


class Expect(BaseModel):
    tools: list[str] = Field(default_factory=list)
    forbidden_tools: list[str] = Field(default_factory=list)
    output_contains: list[str] = Field(default_factory=list)
    max_tool_calls: int | None = None
    max_requests: int | None = None
    no_prompt_drift: bool = False
    max_duration_ms: int | None = None
    max_cost: float | None = None
    max_ttft_ms: int | None = None


class CorpusRow(BaseModel):
    id: str
    inbound: InboundSpec
    expect: Expect = Field(default_factory=Expect)
    cassette: str | None = None
    # Additional turns run against the same session as ``inbound`` (multi-turn).
    turns: list[InboundSpec] = Field(default_factory=list)
    # Files seeded into the row workspace before the first turn, by relative path.
    files: dict[str, str] = Field(default_factory=dict)
    # Deterministic HITL decision for the row: "approve" | "deny" | None.
    hitl_decision: str | None = None

    def effective_turns(self) -> list[InboundSpec]:
        return [self.inbound, *self.turns]


def load_corpus(directory: Path) -> list[CorpusRow]:
    rows: list[CorpusRow] = []
    for path in sorted(directory.glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                data = json.loads(line)
            except ValueError:
                continue
            # Cassette files share the directory; they are not corpus rows.
            if isinstance(data, dict) and "request_digest" in data:
                continue
            rows.append(CorpusRow.model_validate(data))
    return rows


def dump_corpus(rows: list[CorpusRow], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(row.model_dump_json() + "\n")


def mine_log(log_path: Path, *, limit: int | None = None) -> list[CorpusRow]:
    """Extract draft corpus rows from a ``lattice.log`` file."""
    turns: OrderedDict[str, dict[str, Any]] = OrderedDict()
    if not log_path.is_file():
        return []
    for raw in log_path.read_text(encoding="utf-8", errors="replace").splitlines():
        match = _LOG_LINE_RE.search(raw)
        if not match:
            continue
        turn_id, body = match.group(1), match.group(2)
        acc = turns.setdefault(
            turn_id,
            {"inbound": None, "tools": [], "channel": "cli", "profile": "default", "end": False},
        )
        if body.startswith("BEGIN "):
            for key, value in re.findall(r"(\w+)=(\S+)", body):
                if key == "channel":
                    acc["channel"] = value
                elif key == "profile":
                    acc["profile"] = value
        elif body.startswith("inbound: "):
            acc["inbound"] = body[len("inbound: ") :]
        elif body.startswith("tool_start: "):
            name = body[len("tool_start: ") :].split(" ", 1)[0]
            acc["tools"].append(name)
        elif body.startswith("END "):
            acc["end"] = True

    rows: list[CorpusRow] = []
    for turn_id, acc in turns.items():
        if not acc["inbound"]:
            continue
        rows.append(
            CorpusRow(
                id=f"mined-{turn_id}",
                inbound=InboundSpec(
                    text=acc["inbound"],
                    channel=acc["channel"] or "cli",
                    profile_id=acc["profile"] or "default",
                ),
                expect=Expect(tools=list(dict.fromkeys(acc["tools"]))),
                cassette=None,
            )
        )
        if limit is not None and len(rows) >= limit:
            break
    return rows


def mine_shell_commands(audit_path: Path) -> list[str]:
    """Shell commands from ``audit.jsonl`` — seeds the adversarial shell-guard set."""
    commands: list[str] = []
    if not audit_path.is_file():
        return commands
    for raw in audit_path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            record = json.loads(raw)
        except ValueError:
            continue
        if record.get("event") == "tool" and record.get("name") == "shell":
            command = record.get("command")
            if isinstance(command, str) and command.strip():
                commands.append(command)
    return list(dict.fromkeys(commands))
