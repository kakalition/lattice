"""MCP bridge + scheduler smoke tests."""

from __future__ import annotations

from pathlib import Path

from lattice.config import McpDeferMode, ToolsConfig
from lattice.mcp import (
    McpHostManager,
    McpToolInfo,
    should_defer_mcp,
    tool_describe,
    tool_search,
)
from lattice.scheduler import Job, job_to_inbound, load_jobs, save_jobs


def test_mcp_defer_and_bridge() -> None:
    mgr = McpHostManager()
    mgr.register_discovered(
        [
            McpToolInfo(server="s", name=f"t{i}", description=f"tool {i}")
            for i in range(10)
        ]
    )
    cfg = ToolsConfig(mcp_defer=McpDeferMode.AUTO, mcp_defer_threshold=8)
    assert should_defer_mcp(mgr, cfg)
    assert "t3" in tool_search(mgr, "t3")
    assert "schema" in tool_describe(mgr, "t3")


def test_scheduler_jobs_roundtrip(tmp_path: Path) -> None:
    jobs = [
        Job(id="j1", prompt="ping", profile="finance", deliver="none", schedule="0 * * * *")
    ]
    save_jobs(jobs, tmp_path)
    loaded = load_jobs(tmp_path)
    assert loaded[0].profile == "finance"
    inbound = job_to_inbound(loaded[0])
    assert inbound.channel == "scheduler"
    assert inbound.profile_id == "finance"


def test_gateway_pidfile(tmp_path: Path) -> None:
    from lattice.gateway import PidfileLock

    lock = PidfileLock(tmp_path / "gateway.pid")
    lock.acquire()
    assert (tmp_path / "gateway.pid").exists()
    lock.release()
    assert not (tmp_path / "gateway.pid").exists()
