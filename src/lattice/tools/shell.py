"""Shell tool — local subprocess with timeout."""

from __future__ import annotations

import asyncio
import os

from pydantic import BaseModel

from lattice.hitl.policies import unbounded_scan_reason
from lattice.runtime import get_cwd
from lattice.tools.deadline import with_deadline

DEFAULT_TIMEOUT_S = 30.0
MAX_TIMEOUT_S = 120.0


class ShellResult(BaseModel):
    exit_code: int
    stdout: str
    stderr: str


async def run_shell(command: str, *, timeout: float = DEFAULT_TIMEOUT_S) -> ShellResult:
    reason = unbounded_scan_reason(command)
    if reason is not None:
        raise ValueError(reason)
    timeout = max(1.0, min(float(timeout), MAX_TIMEOUT_S))
    cwd = get_cwd()
    proc = await asyncio.create_subprocess_shell(
        command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(cwd),
        env=os.environ.copy(),
        start_new_session=True,
    )

    async def _wait() -> ShellResult:
        stdout_b, stderr_b = await proc.communicate()
        return ShellResult(
            exit_code=proc.returncode or 0,
            stdout=stdout_b.decode("utf-8", errors="replace")[:20_000],
            stderr=stderr_b.decode("utf-8", errors="replace")[:20_000],
        )

    try:
        return await with_deadline(_wait(), seconds=timeout, label="shell")
    except TimeoutError:
        try:
            os.killpg(proc.pid, 9)
        except (ProcessLookupError, PermissionError, OSError):
            proc.kill()
        raise
