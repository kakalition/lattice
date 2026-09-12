"""Compile-on-write and large-read heading."""

from __future__ import annotations

import asyncio
from pathlib import Path

from lattice.deps import truncate_result
from lattice.tools.file import READ_MAX_LINES, read_file, write_file


def test_valid_python_reports_compile_ok(tmp_path: Path) -> None:
    out = asyncio.run(write_file("ok.py", "x = 1\n", workspace=tmp_path))
    assert out.startswith("wrote ")
    assert "compile: ok" in out


def test_python_syntax_error_is_reported_inline(tmp_path: Path) -> None:
    out = asyncio.run(write_file("bad.py", "def f(:\n", workspace=tmp_path))
    assert "compile error" in out


def test_non_script_file_is_unchanged(tmp_path: Path) -> None:
    out = asyncio.run(write_file("notes.txt", "hello\n", workspace=tmp_path))
    assert out.startswith("wrote ")
    assert "compile" not in out


def test_read_file_heads_large_files(tmp_path: Path) -> None:
    big = tmp_path / "big.txt"
    body = "\n".join(f"line {i}" for i in range(READ_MAX_LINES + 500))
    big.write_text(body)

    out = asyncio.run(read_file("big.txt", workspace=tmp_path))

    assert "line 0" in out
    assert "truncated:" in out
    assert "500 hidden" in out


def test_read_file_small_file_untouched(tmp_path: Path) -> None:
    (tmp_path / "small.txt").write_text("a\nb\n")
    assert asyncio.run(read_file("small.txt", workspace=tmp_path)) == "a\nb\n"


def test_truncate_result_default_is_trimmed() -> None:
    assert truncate_result("x" * 20_000).endswith("[truncated]")
    assert len(truncate_result("x" * 20_000)) == 12_000 + len("\n[truncated]")
