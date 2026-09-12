#!/usr/bin/env python3
"""List Lattice profiles (stdlib only). Bundled with the `profile-authoring` skill.

Usage:
  profiles.py list
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def home_dir() -> Path:
    env = os.environ.get("LATTICE_HOME")
    if env:
        return Path(env).expanduser()
    return Path.cwd() / ".lattice"


def list_profiles() -> list[str]:
    root = home_dir() / "profiles"
    if not root.is_dir():
        return []
    return sorted(p.name for p in root.iterdir() if p.is_dir() and (p / "profile.yaml").exists())


def cmd_list(args: argparse.Namespace) -> int:
    names = list_profiles()
    print("\n".join(names) if names else "(no profiles)")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="profiles", description="Lattice profiles")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list", help="list profile ids").set_defaults(func=cmd_list)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except OSError as exc:
        print(f"profiles error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
