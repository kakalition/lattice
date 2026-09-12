"""Regression: Telegram polling must work inside asyncio.run (no nested loop)."""

from __future__ import annotations

import ast
from pathlib import Path


def test_telegram_bot_does_not_call_run_polling() -> None:
    """PTB Application.run_polling() cannot nest under asyncio.run — use start_polling."""
    src = Path(__file__).resolve().parents[1] / "src/lattice/channel/telegram/bot.py"
    tree = ast.parse(src.read_text(encoding="utf-8"))
    calls: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr == "run_polling":
                calls.append("run_polling")
            if isinstance(func, ast.Attribute) and func.attr == "start_polling":
                calls.append("start_polling")
    assert "run_polling" not in calls
    assert "start_polling" in calls


def test_soul_and_name_commands_are_registered() -> None:
    from lattice.channel.telegram.commands import COMMANDS

    names = {name for name, _ in COMMANDS}
    assert {"soul", "name"} <= names

    src = Path(__file__).resolve().parents[1] / "src/lattice/channel/telegram/bot.py"
    tree = ast.parse(src.read_text(encoding="utf-8"))
    handlers: list[str] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "CommandHandler"
            and node.args
            and isinstance(node.args[0], ast.Constant)
        ):
            handlers.append(str(node.args[0].value))
    assert {"soul", "name"} <= set(handlers)
