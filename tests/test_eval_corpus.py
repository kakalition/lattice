"""Corpus parser and miner."""

from __future__ import annotations

import json
from pathlib import Path

from lattice.eval.corpus import mine_log, mine_shell_commands


def test_mine_log_extracts_turns(tmp_path: Path) -> None:
    log = tmp_path / "lattice.log"
    log.write_text(
        "2026-09-12T10:00:00+0700 INFO [lattice.turn] turn=aaa111 BEGIN "
        "channel=telegram user=7 profile=finance session=s1\n"
        "2026-09-12T10:00:00+0700 INFO [lattice.turn] turn=aaa111 inbound: show my balance\n"
        "2026-09-12T10:00:01+0700 INFO [lattice.turn] turn=aaa111 tool_start: "
        "sqlite_schema args={'name': 'finance'}\n"
        "2026-09-12T10:00:02+0700 INFO [lattice.turn] turn=aaa111 END duration_ms=5\n"
        "2026-09-12T10:01:00+0700 INFO [lattice.turn] turn=bbb222 BEGIN channel=cli "
        "user=local profile=default session=s2\n"
        "2026-09-12T10:01:00+0700 INFO [lattice.turn] turn=bbb222 inbound: hello\n"
        "2026-09-12T10:01:01+0700 INFO [lattice.turn] turn=bbb222 END duration_ms=3\n",
        encoding="utf-8",
    )
    rows = mine_log(log)
    assert len(rows) == 2
    assert rows[0].inbound.text == "show my balance"
    assert rows[0].inbound.channel == "telegram"
    assert rows[0].inbound.profile_id == "finance"
    assert rows[0].expect.tools == ["sqlite_schema"]
    assert rows[1].id == "mined-bbb222"


def test_mine_shell_commands_from_audit(tmp_path: Path) -> None:
    audit = tmp_path / "audit.jsonl"
    audit.write_text(
        json.dumps({"event": "tool", "name": "shell", "command": "find / -name x"})
        + "\n"
        + json.dumps({"event": "tool", "name": "read_file", "path": "x"})
        + "\n"
        + json.dumps({"event": "tool", "name": "shell", "command": "find / -name x"})
        + "\n",
        encoding="utf-8",
    )
    assert mine_shell_commands(audit) == ["find / -name x"]


def test_load_corpus_skips_cassette_lines(tmp_path: Path) -> None:
    from lattice.eval.corpus import load_corpus

    (tmp_path / "rows.jsonl").write_text(
        '{"id":"r1","inbound":{"text":"hi"},"expect":{},"cassette":"c.jsonl"}\n',
        encoding="utf-8",
    )
    (tmp_path / "c.jsonl").write_text(
        json.dumps({"call": 1, "request_digest": "abc", "request_json": []}) + "\n",
        encoding="utf-8",
    )
    rows = load_corpus(tmp_path)
    assert [r.id for r in rows] == ["r1"]
