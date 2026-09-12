"""SQLite session store with profile_id and compression lineage."""

from __future__ import annotations

import asyncio
import json
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import aiosqlite

from lattice.paths import lattice_home
from lattice.sqlite.pragmas import apply_perf_pragmas

_LOCK = asyncio.Lock()
# Per-session locks serialize read-modify-write of a session blob so two
# concurrent turns cannot clobber each other's messages/actions.
_LOCKS_GUARD = threading.Lock()
_session_locks: dict[str, asyncio.Lock] = {}


def _session_lock(session_id: str) -> asyncio.Lock:
    with _LOCKS_GUARD:
        lock = _session_locks.get(session_id)
        if lock is None:
            lock = asyncio.Lock()
            _session_locks[session_id] = lock
        return lock


async def _ensure_column(
    conn: aiosqlite.Connection, table: str, column: str, declaration: str
) -> None:
    """Idempotent ``ALTER TABLE`` guarded by ``PRAGMA table_info``."""
    cur = await conn.execute(f"PRAGMA table_info({table})")
    rows = await cur.fetchall()
    if column not in {row[1] for row in rows}:
        await conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")


class SessionStore:
    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = db_path or (lattice_home() / "state.db")

    async def connect(self) -> aiosqlite.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = await aiosqlite.connect(self.db_path)
        await apply_perf_pragmas(conn)
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY,
                profile_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                channel TEXT NOT NULL,
                parent_id TEXT,
                title TEXT,
                messages_json TEXT NOT NULL DEFAULT '[]',
                usage_json TEXT NOT NULL DEFAULT '{}',
                actions_json TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        await _ensure_column(conn, "sessions", "actions_json", "TEXT NOT NULL DEFAULT '[]'")
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sticky_profiles (
                channel TEXT NOT NULL,
                user_id TEXT NOT NULL,
                profile_id TEXT NOT NULL,
                PRIMARY KEY (channel, user_id)
            )
            """
        )
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sticky_primary_models (
                channel TEXT NOT NULL,
                user_id TEXT NOT NULL,
                model_id TEXT NOT NULL,
                PRIMARY KEY (channel, user_id)
            )
            """
        )
        await conn.execute(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS sessions_fts USING fts5(
                id, title, messages_json, content='sessions', content_rowid='rowid'
            )
            """
        )
        await conn.commit()
        return conn

    async def create(
        self,
        *,
        profile_id: str,
        user_id: str,
        channel: str,
        parent_id: str | None = None,
        session_id: str | None = None,
    ) -> str:
        sid = session_id or str(uuid.uuid4())
        now = datetime.now(UTC).isoformat()
        async with _LOCK:
            conn = await self.connect()
            try:
                await conn.execute(
                    """
                    INSERT INTO sessions
                    (id, profile_id, user_id, channel, parent_id, title, messages_json, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, '[]', ?, ?)
                    """,
                    (sid, profile_id, user_id, channel, parent_id, None, now, now),
                )
                await conn.commit()
            finally:
                await conn.close()
        return sid

    async def get(self, session_id: str) -> dict[str, Any] | None:
        conn = await self.connect()
        try:
            conn.row_factory = aiosqlite.Row
            cur = await conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,))
            row = await cur.fetchone()
            if not row:
                return None
            data = dict(row)
            data["messages"] = json.loads(data.pop("messages_json") or "[]")
            data["usage"] = json.loads(data.pop("usage_json") or "{}")
            data["actions"] = json.loads(data.pop("actions_json") or "[]")
            return data
        finally:
            await conn.close()

    async def save_messages(
        self,
        session_id: str,
        messages: list[dict[str, Any]],
        *,
        usage: dict[str, Any] | None = None,
    ) -> None:
        now = datetime.now(UTC).isoformat()
        async with _session_lock(session_id), _LOCK:
            conn = await self.connect()
            try:
                await conn.execute(
                    """
                    UPDATE sessions
                    SET messages_json = ?, usage_json = COALESCE(?, usage_json), updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        json.dumps(messages, ensure_ascii=False),
                        json.dumps(usage or {}, ensure_ascii=False) if usage is not None else None,
                        now,
                        session_id,
                    ),
                )
                await conn.commit()
            finally:
                await conn.close()

    async def append_message(
        self,
        session_id: str,
        message: dict[str, Any],
        *,
        usage: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Atomically read-modify-write one message under the per-session lock.

        Returns the full post-append message list. Two concurrent appends both
        survive instead of one clobbering the other's blob.
        """
        now = datetime.now(UTC).isoformat()
        async with _session_lock(session_id), _LOCK:
            conn = await self.connect()
            try:
                conn.row_factory = aiosqlite.Row
                cur = await conn.execute(
                    "SELECT messages_json FROM sessions WHERE id = ?", (session_id,)
                )
                row = await cur.fetchone()
                messages = json.loads(row["messages_json"]) if row and row["messages_json"] else []
                messages.append(message)
                await conn.execute(
                    """
                    UPDATE sessions
                    SET messages_json = ?, usage_json = COALESCE(?, usage_json), updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        json.dumps(messages, ensure_ascii=False),
                        json.dumps(usage or {}, ensure_ascii=False) if usage is not None else None,
                        now,
                        session_id,
                    ),
                )
                await conn.commit()
                return messages
            finally:
                await conn.close()

    async def append_actions(
        self,
        session_id: str,
        new: list[Any],
        *,
        max_keep: int = 20,
    ) -> None:
        """Append bounded action-ledger records (oldest dropped)."""
        if not new:
            return
        now = datetime.now(UTC).isoformat()
        payload = [item.model_dump() if hasattr(item, "model_dump") else dict(item) for item in new]
        async with _session_lock(session_id), _LOCK:
            conn = await self.connect()
            try:
                conn.row_factory = aiosqlite.Row
                cur = await conn.execute(
                    "SELECT actions_json FROM sessions WHERE id = ?", (session_id,)
                )
                row = await cur.fetchone()
                existing = json.loads(row["actions_json"]) if row and row["actions_json"] else []
                existing.extend(payload)
                existing = existing[-max_keep:]
                await conn.execute(
                    "UPDATE sessions SET actions_json = ?, updated_at = ? WHERE id = ?",
                    (json.dumps(existing, ensure_ascii=False), now, session_id),
                )
                await conn.commit()
            finally:
                await conn.close()

    async def list_sessions(
        self,
        *,
        profile_id: str | None = None,
        user_id: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        conn = await self.connect()
        try:
            conn.row_factory = aiosqlite.Row
            clauses: list[str] = []
            args: list[Any] = []
            if profile_id:
                clauses.append("profile_id = ?")
                args.append(profile_id)
            if user_id:
                clauses.append("user_id = ?")
                args.append(user_id)
            where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
            cur = await conn.execute(
                f"""
                SELECT id, profile_id, user_id, channel, parent_id, title, created_at, updated_at
                FROM sessions {where}
                ORDER BY updated_at DESC LIMIT ?
                """,
                [*args, limit],
            )
            rows = await cur.fetchall()
            return [dict(r) for r in rows]
        finally:
            await conn.close()

    async def search(self, query: str, *, limit: int = 10) -> list[dict[str, Any]]:
        conn = await self.connect()
        try:
            conn.row_factory = aiosqlite.Row
            like = f"%{query}%"
            cur = await conn.execute(
                """
                SELECT id, profile_id, title, updated_at, messages_json
                FROM sessions
                WHERE messages_json LIKE ? OR IFNULL(title, '') LIKE ?
                ORDER BY updated_at DESC LIMIT ?
                """,
                (like, like, limit),
            )
            rows = await cur.fetchall()
            out: list[dict[str, Any]] = []
            for row in rows:
                d = dict(row)
                d.pop("messages_json", None)
                out.append(d)
            return out
        finally:
            await conn.close()

    async def set_sticky_profile(self, channel: str, user_id: str, profile_id: str) -> None:
        async with _LOCK:
            conn = await self.connect()
            try:
                await conn.execute(
                    """
                    INSERT INTO sticky_profiles (channel, user_id, profile_id)
                    VALUES (?, ?, ?)
                    ON CONFLICT(channel, user_id) DO UPDATE SET profile_id = excluded.profile_id
                    """,
                    (channel, user_id, profile_id),
                )
                await conn.commit()
            finally:
                await conn.close()

    async def get_sticky_profile(self, channel: str, user_id: str) -> str | None:
        conn = await self.connect()
        try:
            cur = await conn.execute(
                "SELECT profile_id FROM sticky_profiles WHERE channel = ? AND user_id = ?",
                (channel, user_id),
            )
            row = await cur.fetchone()
            return row[0] if row else None
        finally:
            await conn.close()

    async def clear_sticky_for_profile(self, profile_id: str) -> int:
        """Clear sticky mappings that pointed at a removed profile. Returns rows cleared."""
        async with _LOCK:
            conn = await self.connect()
            try:
                cur = await conn.execute(
                    "DELETE FROM sticky_profiles WHERE profile_id = ?",
                    (profile_id,),
                )
                await conn.commit()
                return cur.rowcount if cur.rowcount is not None and cur.rowcount >= 0 else 0
            finally:
                await conn.close()

    async def set_sticky_primary_model(self, channel: str, user_id: str, model_id: str) -> None:
        async with _LOCK:
            conn = await self.connect()
            try:
                await conn.execute(
                    """
                    INSERT INTO sticky_primary_models (channel, user_id, model_id)
                    VALUES (?, ?, ?)
                    ON CONFLICT(channel, user_id) DO UPDATE SET model_id = excluded.model_id
                    """,
                    (channel, user_id, model_id),
                )
                await conn.commit()
            finally:
                await conn.close()

    async def get_sticky_primary_model(self, channel: str, user_id: str) -> str | None:
        conn = await self.connect()
        try:
            cur = await conn.execute(
                "SELECT model_id FROM sticky_primary_models WHERE channel = ? AND user_id = ?",
                (channel, user_id),
            )
            row = await cur.fetchone()
            return row[0] if row else None
        finally:
            await conn.close()

    async def clear_sticky_primary_model(self, channel: str, user_id: str) -> None:
        async with _LOCK:
            conn = await self.connect()
            try:
                await conn.execute(
                    "DELETE FROM sticky_primary_models WHERE channel = ? AND user_id = ?",
                    (channel, user_id),
                )
                await conn.commit()
            finally:
                await conn.close()


def sanitize_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Resume sanitization: drop surrogates and repair orphan tool pairs."""
    cleaned: list[dict[str, Any]] = []
    pending_tool_ids: set[str] = set()
    for msg in messages:
        role = msg.get("role")
        content = msg.get("content")
        if isinstance(content, str):
            content = content.encode("utf-8", "surrogatepass").decode("utf-8", "ignore")
            msg = {**msg, "content": content}
        if role == "assistant" and msg.get("tool_calls"):
            for tc in msg["tool_calls"]:
                tid = tc.get("id")
                if tid:
                    pending_tool_ids.add(tid)
            cleaned.append(msg)
            continue
        if role == "tool":
            tid = msg.get("tool_call_id")
            if tid and tid in pending_tool_ids:
                pending_tool_ids.discard(tid)
                cleaned.append(msg)
            continue
        cleaned.append(msg)
    # Close interrupted tool pairs
    if pending_tool_ids:
        for tid in list(pending_tool_ids):
            cleaned.append(
                {
                    "role": "tool",
                    "tool_call_id": tid,
                    "content": "[interrupted — tool result missing]",
                }
            )
    return cleaned
