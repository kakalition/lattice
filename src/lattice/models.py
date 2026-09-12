"""Channel-facing inbound/outbound message models."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


class Button(BaseModel):
    label: str
    data: str


class Inbound(BaseModel):
    text: str
    profile_id: str = "default"
    user_id: str = "local"
    channel: str = "cli"
    session_id: str | None = None
    media_paths: list[Path] = Field(default_factory=list)
    cancel: bool = False
    steer_text: str | None = None
    # Per-turn cancellation token (asyncio.Event); typed loosely so the model
    # stays serialization-friendly.
    cancel_event: Any | None = None


class Outbound(BaseModel):
    text: str
    session_id: str | None = None
    buttons: list[Button] | None = None
    media_paths: list[Path] | None = None
    edit_message_id: int | None = None
    profile_id: str | None = None
