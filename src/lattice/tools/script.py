"""Sandboxed local script execution (bwrap when available)."""

from __future__ import annotations

import asyncio
import os
import shutil
import tempfile
from pathlib import Path

from pydantic import BaseModel

from lattice.config import ScriptsConfig
from lattice.paths import lattice_home
from lattice.tools.deadline import with_deadline
from lattice.tools.file_safety import resolve_agent_path

LANG_EXTS = {"python": ".py", "node": ".js", "bash": ".sh"}
LANG_BINARIES = {"python": ("python3", "python"), "node": ("node",), "bash": ("bash",)}


class ScriptResult(BaseModel):
    exit_code: int
    stdout: str
    stderr: str
    sandbox: str
    path: str


def bwrap_available() -> bool:
    return shutil.which("bwrap") is not None


def scripts_dir(home: Path | None = None) -> Path:
    root = (home or lattice_home()) / "scripts"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _resolve_interpreter(language: str) -> str | None:
    for name in LANG_BINARIES.get(language, ()):
        path = shutil.which(name)
        if path:
            return path
    return None


def _host_ro_binds() -> list[str]:
    """Minimal host paths to bind read-only inside bwrap."""
    candidates = [
        "/usr",
        "/bin",
        "/lib",
        "/lib64",
        "/sbin",
        "/etc/ssl",
        "/etc/ca-certificates",
        "/System/Library",  # unlikely on Linux; harmless if missing
        "/Library/Developer",
        "/opt/homebrew",
        "/usr/local",
    ]
    # pyenv / nvm / uv toolchains often live under HOME — bind the interp's parents below.
    return [p for p in candidates if Path(p).exists()]


def _ancestor_binds(path: Path) -> list[str]:
    """Bind ancestors of interpreter so dynamic linkers / node modules resolve."""
    binds: list[str] = []
    resolved = path.resolve()
    # Bind the interpreter file's top-level prefix (e.g. /Users/x/.nvm)
    parts = resolved.parts
    if len(parts) >= 3:
        # /Users/name/.nvm → bind that; /home/x/.local → bind
        for i in range(2, min(len(parts), 6)):
            candidate = Path(*parts[: i + 1])
            if candidate.is_dir() and str(candidate) not in binds:
                # Prefer deeper stable roots only
                pass
        # Practical: bind parent of binary and its grandparent if under home
        for parent in (resolved.parent, resolved.parent.parent, resolved.parent.parent.parent):
            if parent.exists() and str(parent) not in ("/", ""):
                binds.append(str(parent))
    return binds


def build_bwrap_command(
    *,
    interpreter: str,
    script_path: Path,
    workspace: Path,
    scripts_root: Path,
    allow_network: bool,
    argv_extra: list[str] | None = None,
) -> list[str]:
    cmd: list[str] = ["bwrap", "--die-with-parent", "--new-session"]
    if not allow_network:
        cmd.append("--unshare-net")
    cmd.extend(["--unshare-pid", "--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp"])

    bound: set[str] = set()
    for path in _host_ro_binds() + _ancestor_binds(Path(interpreter)):
        if path in bound or not Path(path).exists():
            continue
        cmd.extend(["--ro-bind", path, path])
        bound.add(path)

    # Essential: bind interpreter explicitly if not already covered
    interp = str(Path(interpreter).resolve())
    if not any(interp.startswith(b.rstrip("/") + "/") or interp == b for b in bound):
        cmd.extend(["--ro-bind", interp, interp])

    cmd.extend(
        [
            "--bind",
            str(workspace.resolve()),
            str(workspace.resolve()),
            "--bind",
            str(scripts_root.resolve()),
            str(scripts_root.resolve()),
            "--chdir",
            str(workspace.resolve()),
            "--",
            interp,
            str(script_path.resolve()),
            *(argv_extra or []),
        ]
    )
    return cmd


def build_soft_command(
    *,
    interpreter: str,
    script_path: Path,
    argv_extra: list[str] | None = None,
) -> list[str]:
    return [interpreter, str(script_path.resolve()), *(argv_extra or [])]


async def execute_script(
    *,
    language: str,
    code: str | None = None,
    path: str | None = None,
    timeout: float = 60.0,
    workspace: Path,
    home: Path | None = None,
    cfg: ScriptsConfig | None = None,
    argv_extra: list[str] | None = None,
) -> ScriptResult:
    cfg = cfg or ScriptsConfig()
    language = (language or "").strip().lower()
    if language not in LANG_EXTS:
        raise ValueError(f"unsupported language {language!r}; use python, node, or bash")
    if language not in cfg.languages:
        raise ValueError(f"language {language!r} disabled in scripts.languages")

    home = home or lattice_home()
    scripts_root = scripts_dir(home)
    interpreter = _resolve_interpreter(language)
    if not interpreter:
        raise RuntimeError(f"interpreter for {language} not found on PATH")

    # Resolve script source
    cleanup: Path | None = None
    if path:
        script_path = resolve_agent_path(path, workspace, home=home)
        if not script_path.is_file():
            raise FileNotFoundError(f"script not found: {script_path}")
    elif code is not None and str(code).strip():
        run_dir = scripts_root / ".run"
        run_dir.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix="lattice_", suffix=LANG_EXTS[language], dir=run_dir)
        os.close(fd)
        cleanup = Path(tmp)
        cleanup.write_text(code, encoding="utf-8")
        if language == "bash":
            cleanup.chmod(0o700)
        script_path = cleanup
    else:
        raise ValueError("execute_script requires code= or path=")

    use_bwrap = bwrap_available()
    if cfg.require_bwrap and not use_bwrap:
        if cleanup:
            cleanup.unlink(missing_ok=True)
        raise RuntimeError(
            "execute_script requires bwrap (bubblewrap) but it was not found. "
            "Install bubblewrap on Linux, or set scripts.require_bwrap: false for soft sandbox."
        )

    timeout = float(timeout or cfg.timeout_seconds)
    timeout = max(1.0, min(timeout, float(cfg.max_timeout_seconds)))

    if use_bwrap:
        cmd = build_bwrap_command(
            interpreter=interpreter,
            script_path=script_path,
            workspace=workspace,
            scripts_root=scripts_root,
            allow_network=cfg.allow_network,
            argv_extra=argv_extra,
        )
        sandbox = "bwrap"
    else:
        cmd = build_soft_command(
            interpreter=interpreter, script_path=script_path, argv_extra=argv_extra
        )
        sandbox = "soft"

    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "LANG": os.environ.get("LANG", "C.UTF-8"),
        "HOME": str(scripts_root / ".home"),
        "TMPDIR": "/tmp" if use_bwrap else str(scripts_root / ".tmp"),
        "LATTICE_SCRIPTS": str(scripts_root),
        "LATTICE_WORKSPACE": str(workspace.resolve()),
    }
    (scripts_root / ".home").mkdir(parents=True, exist_ok=True)
    (scripts_root / ".tmp").mkdir(parents=True, exist_ok=True)

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(workspace.resolve()),
            env=env,
            start_new_session=True,
        )

        async def _wait() -> ScriptResult:
            out_b, err_b = await proc.communicate()
            return ScriptResult(
                exit_code=proc.returncode or 0,
                stdout=out_b.decode("utf-8", errors="replace")[:50_000],
                stderr=err_b.decode("utf-8", errors="replace")[:20_000],
                sandbox=sandbox,
                path=str(script_path),
            )

        try:
            return await with_deadline(_wait(), seconds=timeout, label="execute_script")
        except TimeoutError:
            try:
                os.killpg(proc.pid, 9)
            except (ProcessLookupError, PermissionError, OSError):
                proc.kill()
            raise
    finally:
        if cleanup is not None:
            cleanup.unlink(missing_ok=True)


def format_script_result(result: ScriptResult) -> str:
    return (
        f"exit={result.exit_code} sandbox={result.sandbox} path={result.path}\n"
        f"{result.stdout}" + (f"\n[stderr]\n{result.stderr}" if result.stderr else "")
    )
