"""Audit log append."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from lattice.paths import lattice_home


def audit_log(event: str, payload: dict[str, Any], home: Path | None = None) -> None:
    path = (home or lattice_home()) / "audit.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "ts": datetime.now(UTC).isoformat(),
        "event": event,
        **payload,
    }
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")
