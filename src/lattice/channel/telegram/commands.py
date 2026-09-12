"""Bot command menu definitions."""

from __future__ import annotations

COMMANDS = [
    ("start", "Start ◇ Lattice"),
    ("help", "Help"),
    ("stop", "Cancel current turn / HITL"),
    ("sessions", "List recent sessions"),
    ("resume", "Resume session by id"),
    ("reset", "Start a new session (keeps the old one)"),
    ("forget", "Forget a memory id"),
    ("model", "Show or set primary model"),
    ("tools", "List enabled tool policy"),
    ("profile", "Switch or remove profile"),
    ("soul", "Show, set, or reset the profile soul"),
    ("name", "Show, set, or reset the assistant name"),
]
