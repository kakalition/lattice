#!/usr/bin/env python3
"""Remove a Lattice profile (destructive; HITL-gated). Bundled with `profile-authoring`.

Usage:
  profile_remove.py remove PROFILE_ID
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
from pathlib import Path

PROFILE_ID_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$")


def home_dir() -> Path:
    env = os.environ.get("LATTICE_HOME")
    if env:
        return Path(env).expanduser()
    return Path.cwd() / ".lattice"


def cmd_remove(args: argparse.Namespace) -> int:
    pid = (args.profile_id or "").strip()
    if not PROFILE_ID_RE.fullmatch(pid):
        print(
            "invalid profile id (use letters, digits, _ or -, max 64, no path separators)",
            file=sys.stderr,
        )
        return 1
    if pid == "default":
        print("cannot remove the default profile", file=sys.stderr)
        return 1
    profiles_root = (home_dir() / "profiles").resolve()
    target = (profiles_root / pid).resolve()
    try:
        target.relative_to(profiles_root)
    except ValueError:
        print("invalid profile path", file=sys.stderr)
        return 1
    if not target.is_dir() or not (target / "profile.yaml").is_file():
        print(f"profile not found: {pid}", file=sys.stderr)
        return 1
    shutil.rmtree(target)
    print(f"removed profile {pid}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="profile_remove", description="Remove a Lattice profile")
    sub = parser.add_subparsers(dest="command", required=True)
    remove = sub.add_parser("remove", help="delete profiles/<id>/")
    remove.add_argument("profile_id")
    remove.set_defaults(func=cmd_remove)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except OSError as exc:
        print(f"profile_remove error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
