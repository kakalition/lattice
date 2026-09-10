"""Timezone detection and persistence for Lattice."""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

import yaml

from lattice.paths import lattice_home, user_config_path


def detect_local_tz_name() -> str:
    """Best-effort IANA timezone for this host (never invent UTC just because .key is missing)."""
    tz = datetime.now().astimezone().tzinfo
    key = getattr(tz, "key", None)
    if isinstance(key, str) and key:
        return key

    for candidate in (Path("/etc/localtime"),):
        try:
            resolved = candidate.resolve()
            parts = resolved.parts
            if "zoneinfo" in parts:
                idx = parts.index("zoneinfo")
                name = "/".join(parts[idx + 1 :])
                if name:
                    return name
        except OSError:
            continue

    for env_key in ("LATTICE_TIMEZONE", "TZ"):
        val = os.environ.get(env_key)
        if val:
            return val.strip()

    offset = datetime.now().astimezone().utcoffset()
    if offset is not None:
        hours = int(offset.total_seconds() // 3600)
        common = {
            7: "Asia/Ho_Chi_Minh",
            8: "Asia/Singapore",
            9: "Asia/Tokyo",
            0: "UTC",
            -5: "America/New_York",
            -8: "America/Los_Angeles",
        }
        if hours in common:
            return common[hours]
    return "UTC"


def _read_timezone_from(path: Path) -> str | None:
    if not path.is_file():
        return None
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return None
    if isinstance(data, dict):
        tz = data.get("timezone")
        if isinstance(tz, str) and tz.strip():
            return tz.strip()
    return None


def read_config_timezone(home: Path | None = None) -> str | None:
    """Prefer visible lattice.yaml; fall back to legacy .lattice/config.yaml."""
    root = home or lattice_home()
    for path in (user_config_path(home), root / "config.yaml"):
        tz = _read_timezone_from(path)
        if tz:
            return tz
    return None


def resolve_timezone(home: Path | None = None, *, explicit: str = "") -> str:
    if explicit.strip():
        return explicit.strip()
    saved = read_config_timezone(home)
    if saved:
        return saved
    return detect_local_tz_name()


def persist_timezone(timezone: str, home: Path | None = None) -> Path:
    """Write timezone into visible lattice.yaml (create/merge)."""
    from lattice.config import merge_yaml_into

    path = user_config_path(home)
    return merge_yaml_into(path, {"timezone": timezone.strip()})


def ensure_timezone(home: Path | None = None) -> str:
    """Return configured timezone, detecting + persisting on first run."""
    existing = read_config_timezone(home)
    if existing:
        # Migrate off legacy hidden config if needed
        user = user_config_path(home)
        if not _read_timezone_from(user):
            persist_timezone(existing, home)
        return existing
    detected = detect_local_tz_name()
    persist_timezone(detected, home)
    return detected
