"""Letter-labeled choice menus (Telegram A/B/C buttons with full text in body)."""

from __future__ import annotations

from collections.abc import Callable


def choice_letter(index: int) -> str:
    """A, B, … Z, then 27, 28, … for overflow."""
    if 0 <= index < 26:
        return chr(ord("A") + index)
    return str(index + 1)


def letter_choice_menu(
    question: str,
    choices: list[str],
    *,
    data_for_index: Callable[[int], str] | None = None,
) -> tuple[str, list[dict[str, str]]]:
    """Full choice text in the message; buttons are A/B/C only."""
    make_data = data_for_index or (lambda i: f"choice:{i}")
    lines = [question.rstrip(), ""]
    buttons: list[dict[str, str]] = []
    for i, choice in enumerate(choices):
        letter = choice_letter(i)
        lines.append(f"{letter}. {choice}")
        buttons.append({"label": letter, "data": make_data(i)})
    return "\n".join(lines).rstrip(), buttons
