"""SQLite session store with profile_id and compression lineage."""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import aiosqlite

from lattice.paths import lattice_home

_LOCK = asyncio.Lock()


class SessionStore:
    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = db_path or (lattice_home() / "state.db")

    async def connect(self) -> aiosqlite.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = await aiosqlite.connect(self.db_path)
        await conn.execute("PRAGMA journal_mode=WAL")
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
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
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
        async with _LOCK:
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
