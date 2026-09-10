"""Inline / reply keyboard helpers."""

from __future__ import annotations

from typing import Any

from lattice.hitl.choice_menu import choice_letter, letter_choice_menu

__all__ = ["choice_letter", "inline_keyboard", "letter_choice_menu"]


def inline_keyboard(buttons: list[dict[str, str]], *, per_row: int = 2) -> dict[str, Any]:
    # Single-letter (or short) labels fit more per row
    if buttons and all(len(b.get("label", "")) <= 2 for b in buttons):
        per_row = max(per_row, 4)
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
