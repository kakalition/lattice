"""A test run must not append to the real project log."""

from __future__ import annotations

from typing import Any


def test_tests_do_not_write_production_log(_isolated_logging: dict[str, Any]) -> None:
    prod = _isolated_logging["prod"]
    if _isolated_logging["mtime"] is None:
        return  # no pre-existing production log to protect
    stat = prod.stat()
    assert stat.st_mtime == _isolated_logging["mtime"]
    assert stat.st_size == _isolated_logging["size"]
