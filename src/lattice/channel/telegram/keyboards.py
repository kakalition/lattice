"""Inline / reply keyboard helpers."""

from __future__ import annotations

from typing import Any


def inline_keyboard(buttons: list[dict[str, str]], *, per_row: int = 2) -> dict[str, Any]:
    rows: list[list[dict[str, str]]] = []
    row: list[dict[str, str]] = []
    for btn in buttons:
        row.append({"text": btn["label"], "callback_data": btn["data"][:64]})
        if len(row) >= per_row:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return {"inline_keyboard": rows}
