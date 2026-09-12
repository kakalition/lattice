#!/usr/bin/env python3
"""Named multi-SQLite manager (stdlib only).

Bundled with the `sqlite-admin` skill; run through Lattice's ``execute_script``.
Registry lives in ``$LATTICE_HOME/sqlite/databases.json`` (written through by the
runtime). Never touches Lattice session ``state.db``.

Usage:
  sqlite.py list
  sqlite.py schema NAME
  sqlite.py query NAME SQL
  sqlite.py execute NAME SQL [--dry-run]
  sqlite.py register NAME PATH [--read-only]
  sqlite.py unregister NAME
  sqlite.py backup NAME

Env: ``LATTICE_SQLITE_ALLOW`` (JSON list; absent = all), ``LATTICE_SQLITE_ROW_LIMIT``.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path

WRITE_RE = re.compile(
    r"^\s*(INSERT|UPDATE|DELETE|REPLACE|CREATE|DROP|ALTER|ATTACH|DETACH|VACUUM|PRAGMA)\b",
    re.I,
)
DENY_SUFFIXES = (".env", ".pem", ".key")
DENY_NAME_PARTS = (".ssh", ".gnupg", "id_rsa", "id_ed25519", "credentials.json", "state.db")


def home_dir() -> Path:
    env = os.environ.get("LATTICE_HOME")
    if env:
        return Path(env).expanduser()
    return Path.cwd() / ".lattice"


def store_path() -> Path:
    return home_dir() / "sqlite" / "databases.json"


def load_registry() -> dict[str, dict]:
    path = store_path()
    if not path.is_file():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    data = raw.get("databases", raw) if isinstance(raw, dict) else {}
    out: dict[str, dict] = {}
    for name, cfg in (data or {}).items():
        if name == "state":
            continue
        if isinstance(cfg, str):
            out[name] = {"path": cfg, "read_only": False}
        elif isinstance(cfg, dict) and cfg.get("path"):
            out[name] = {"path": str(cfg["path"]), "read_only": bool(cfg.get("read_only"))}
    return out


def save_registry(databases: dict[str, dict]) -> None:
    path = store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        name: {"path": cfg["path"], "read_only": bool(cfg.get("read_only"))}
        for name, cfg in sorted(databases.items())
    }
    path.write_text(
        json.dumps({"databases": payload}, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def allowlist() -> list[str] | None:
    raw = os.environ.get("LATTICE_SQLITE_ALLOW")
    if raw is None:
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return [str(x) for x in parsed] if isinstance(parsed, list) else None


def permitted(name: str, allow: list[str] | None) -> str | None:
    if allow is not None and name not in allow:
        return f"sqlite db not allowed for profile: {name}"
    return None


def resolve(name: str, allow: list[str] | None) -> tuple[Path, bool] | None:
    err = permitted(name, allow)
    if err:
        print(err, file=sys.stderr)
        return None
    entry = load_registry().get(name)
    if entry is None:
        print(f"unknown sqlite db: {name}", file=sys.stderr)
        return None
    return Path(entry["path"]), bool(entry.get("read_only"))


def is_denied(path: Path) -> bool:
    resolved = path.expanduser().resolve()
    name = resolved.name.lower()
    if any(part in resolved.parts for part in DENY_NAME_PARTS) or any(
        part in name for part in DENY_NAME_PARTS
    ):
        return True
    return any(name.endswith(suf) for suf in DENY_SUFFIXES)


def connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def cmd_list(args: argparse.Namespace) -> int:
    allow = allowlist()
    entries = load_registry()
    rows = [(n, c) for n, c in entries.items() if allow is None or n in allow]
    if not rows:
        print("(no registered databases)")
        return 0
    for name, cfg in sorted(rows):
        print(f"{name}: {cfg['path']} read_only={bool(cfg.get('read_only'))}")
    return 0


def cmd_schema(args: argparse.Namespace) -> int:
    target = resolve(args.name, allowlist())
    if target is None:
        return 1
    path, _ = target
    conn = connect(path)
    try:
        rows = conn.execute(
            "SELECT type, name, sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY type, name"
        ).fetchall()
    finally:
        conn.close()
    print("\n".join(f"{t} {n}: {sql}" for t, n, sql in rows) or "(empty schema)")
    return 0


def cmd_query(args: argparse.Namespace) -> int:
    if WRITE_RE.match(args.sql or ""):
        print("sqlite_query is read-only; use the execute subcommand for writes", file=sys.stderr)
        return 1
    target = resolve(args.name, allowlist())
    if target is None:
        return 1
    path, _ = target
    limit = int(os.environ.get("LATTICE_SQLITE_ROW_LIMIT") or 500)
    conn = connect(path)
    try:
        cur = conn.execute(args.sql)
        rows = cur.fetchmany(limit + 1)
        truncated = len(rows) > limit
        rows = rows[:limit]
        cols = [d[0] for d in cur.description] if cur.description else []
    finally:
        conn.close()
    lines = ["\t".join(cols)] if cols else []
    for row in rows:
        lines.append("\t".join("" if v is None else str(v) for v in row))
    text = "\n".join(lines)
    if truncated:
        text += "\n[truncated]"
    print(text[:100_000])
    return 0


def cmd_execute(args: argparse.Namespace) -> int:
    target = resolve(args.name, allowlist())
    if target is None:
        return 1
    path, read_only = target
    if read_only:
        print(f"database {args.name} is read_only", file=sys.stderr)
        return 1
    if args.dry_run:
        print(f"dry-run ok: {(args.sql or '')[:200]}")
        return 0
    conn = connect(path)
    try:
        conn.execute(args.sql)
        conn.commit()
    finally:
        conn.close()
    print("ok")
    return 0


def _db_target(name: str, raw_path: str) -> Path:
    target = Path(raw_path).expanduser()
    if not target.is_absolute():
        target = home_dir() / "sqlite" / f"{name}.db"
    return target.resolve()


def cmd_register(args: argparse.Namespace) -> int:
    name = (args.name or "").strip()
    if name == "state":
        print("cannot register session state.db", file=sys.stderr)
        return 1
    target = _db_target(name, args.path)
    if is_denied(target) or target.name == "state.db":
        print("path denied for sqlite register", file=sys.stderr)
        return 1
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        target.touch()
    databases = load_registry()
    databases[name] = {"path": str(target), "read_only": bool(args.read_only)}
    save_registry(databases)
    print(f"registered {name} -> {target}")
    return 0


def cmd_unregister(args: argparse.Namespace) -> int:
    databases = load_registry()
    if args.name not in databases:
        print(f"unknown sqlite db: {args.name}", file=sys.stderr)
        return 1
    databases.pop(args.name, None)
    save_registry(databases)
    print(f"unregistered {args.name}")
    return 0


def cmd_backup(args: argparse.Namespace) -> int:
    target = resolve(args.name, allowlist())
    if target is None:
        return 1
    path, _ = target
    backup_dir = home_dir() / "sqlite" / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    dest = backup_dir / f"{args.name}-{stamp}.db"
    shutil.copy2(path, dest)
    print(str(dest))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sqlite", description="Lattice named SQLite manager")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="list registered databases").set_defaults(func=cmd_list)

    schema = sub.add_parser("schema", help="show tables/indexes/DDL")
    schema.add_argument("name")
    schema.set_defaults(func=cmd_schema)

    query = sub.add_parser("query", help="read-only SQL (SELECT/CTE)")
    query.add_argument("name")
    query.add_argument("sql")
    query.set_defaults(func=cmd_query)

    execute = sub.add_parser("execute", help="run DDL/DML (gated by HITL)")
    execute.add_argument("name")
    execute.add_argument("sql")
    execute.add_argument("--dry-run", dest="dry_run", action="store_true")
    execute.set_defaults(func=cmd_execute)

    register = sub.add_parser("register", help="add a named database")
    register.add_argument("name")
    register.add_argument("path")
    register.add_argument("--read-only", dest="read_only", action="store_true")
    register.set_defaults(func=cmd_register)

    unregister = sub.add_parser("unregister", help="remove a named database")
    unregister.add_argument("name")
    unregister.set_defaults(func=cmd_unregister)

    backup = sub.add_parser("backup", help="copy a database into sqlite/backups")
    backup.add_argument("name")
    backup.set_defaults(func=cmd_backup)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except (OSError, ValueError, sqlite3.Error, json.JSONDecodeError) as exc:
        print(f"sqlite error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
