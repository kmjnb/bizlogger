from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable

import asyncpg


@dataclass
class StoredMessage:
    business_connection_id: str
    chat_id: int
    message_id: int
    from_id: int | None
    from_name: str | None
    text: str | None
    media_type: str | None
    file_id: str | None
    local_path: str | None
    is_self_destruct: bool
    created_at: int
    edited_at: int | None
    deleted_at: int | None
    edit_history: str | None
    width: int | None = None
    height: int | None = None
    duration: int | None = None
    chat_label: str | None = None
    captured_via_reply: bool = False


def _ts(epoch: int | None) -> datetime | None:
    if epoch is None:
        return None
    return datetime.fromtimestamp(int(epoch), tz=timezone.utc)


def _epoch(ts: datetime | None) -> int | None:
    if ts is None:
        return None
    return int(ts.timestamp())


def _history_to_jsonb(value: str | None):
    if value is None:
        return None
    if isinstance(value, list):
        return json.dumps(value)
    return json.dumps([value])


def _history_from_jsonb(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        try:
            arr = json.loads(value)
        except Exception:
            return value
    else:
        arr = value
    if isinstance(arr, list):
        return "\n---\n".join(str(x) for x in arr if x is not None) or None
    return str(arr)


class Db:
    """Postgres-backed store. Tables are managed by the admin app's Drizzle migrations."""

    def __init__(self, dsn: str):
        self.dsn = dsn
        self._pool: asyncpg.Pool | None = None

    async def init(self) -> None:
        self._pool = await asyncpg.create_pool(self.dsn, min_size=1, max_size=5)

    @property
    def pool(self) -> asyncpg.Pool:
        if self._pool is None:
            raise RuntimeError("Db.init() not called")
        return self._pool

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    async def get_owner_id(self, connection_id: str) -> int | None:
        row = await self.pool.fetchrow(
            "SELECT user_id FROM business_connections WHERE business_connection_id=$1",
            connection_id,
        )
        return int(row["user_id"]) if row else None

    async def upsert_connection(
        self,
        connection_id: str,
        user_id: int,
        is_enabled: bool,
        can_reply: bool,
    ) -> None:
        await self.pool.execute(
            """
            INSERT INTO business_connections
                (business_connection_id, user_id, is_enabled, can_reply, created_at, updated_at)
            VALUES ($1, $2, $3, $4, NOW(), NOW())
            ON CONFLICT (business_connection_id) DO UPDATE SET
              user_id = EXCLUDED.user_id,
              is_enabled = EXCLUDED.is_enabled,
              can_reply = EXCLUDED.can_reply,
              updated_at = NOW()
            """,
            connection_id, user_id, is_enabled, can_reply,
        )

    async def save_message(self, m: StoredMessage) -> None:
        await self.pool.execute(
            """
            INSERT INTO messages (
                business_connection_id, chat_id, message_id,
                chat_label, from_id, from_name,
                text, media_type, file_id, local_path,
                width, height, duration,
                is_self_destruct, captured_via_reply,
                created_at, edited_at, deleted_at, edit_history
            ) VALUES (
                $1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19::jsonb
            )
            ON CONFLICT (business_connection_id, chat_id, message_id) DO UPDATE SET
                chat_label = COALESCE(EXCLUDED.chat_label, messages.chat_label),
                from_id = COALESCE(EXCLUDED.from_id, messages.from_id),
                from_name = COALESCE(EXCLUDED.from_name, messages.from_name),
                text = COALESCE(EXCLUDED.text, messages.text),
                media_type = COALESCE(EXCLUDED.media_type, messages.media_type),
                file_id = COALESCE(EXCLUDED.file_id, messages.file_id),
                local_path = COALESCE(EXCLUDED.local_path, messages.local_path),
                width = COALESCE(EXCLUDED.width, messages.width),
                height = COALESCE(EXCLUDED.height, messages.height),
                duration = COALESCE(EXCLUDED.duration, messages.duration),
                is_self_destruct = EXCLUDED.is_self_destruct,
                captured_via_reply = EXCLUDED.captured_via_reply,
                edited_at = EXCLUDED.edited_at,
                deleted_at = EXCLUDED.deleted_at,
                edit_history = COALESCE(EXCLUDED.edit_history, messages.edit_history)
            """,
            m.business_connection_id, m.chat_id, m.message_id,
            m.chat_label, m.from_id, m.from_name,
            m.text, m.media_type, m.file_id, m.local_path,
            m.width, m.height, m.duration,
            m.is_self_destruct, m.captured_via_reply,
            _ts(m.created_at), _ts(m.edited_at), _ts(m.deleted_at),
            _history_to_jsonb(m.edit_history),
        )

    async def get_message(
        self, connection_id: str, chat_id: int, message_id: int
    ) -> StoredMessage | None:
        row = await self.pool.fetchrow(
            """
            SELECT business_connection_id, chat_id, message_id, from_id, from_name,
                   text, media_type, file_id, local_path, is_self_destruct,
                   captured_via_reply, chat_label,
                   created_at, edited_at, deleted_at, edit_history,
                   width, height, duration
            FROM messages
            WHERE business_connection_id=$1 AND chat_id=$2 AND message_id=$3
            """,
            connection_id, chat_id, message_id,
        )
        if row is None:
            return None
        return StoredMessage(
            business_connection_id=row["business_connection_id"],
            chat_id=int(row["chat_id"]),
            message_id=int(row["message_id"]),
            from_id=int(row["from_id"]) if row["from_id"] is not None else None,
            from_name=row["from_name"],
            text=row["text"],
            media_type=row["media_type"],
            file_id=row["file_id"],
            local_path=row["local_path"],
            is_self_destruct=bool(row["is_self_destruct"]),
            captured_via_reply=bool(row["captured_via_reply"]),
            chat_label=row["chat_label"],
            created_at=_epoch(row["created_at"]) or 0,
            edited_at=_epoch(row["edited_at"]),
            deleted_at=_epoch(row["deleted_at"]),
            edit_history=_history_from_jsonb(row["edit_history"]),
            width=row["width"],
            height=row["height"],
            duration=row["duration"],
        )

    async def update_local_path(
        self, connection_id: str, chat_id: int, message_id: int, local_path: str
    ) -> None:
        await self.pool.execute(
            """
            UPDATE messages SET local_path=$4
            WHERE business_connection_id=$1 AND chat_id=$2 AND message_id=$3
            """,
            connection_id, chat_id, message_id, local_path,
        )

    async def mark_edited(
        self, connection_id: str, chat_id: int, message_id: int,
        new_text: str | None, edit_history: str | None,
    ) -> None:
        await self.pool.execute(
            """
            UPDATE messages
            SET text=$4, edited_at=NOW(), edit_history=$5::jsonb
            WHERE business_connection_id=$1 AND chat_id=$2 AND message_id=$3
            """,
            connection_id, chat_id, message_id,
            new_text, _history_to_jsonb(edit_history),
        )

    async def track_bot_user(
        self,
        user_id: int,
        username: str | None,
        full_name: str | None,
        language_code: str | None,
    ) -> None:
        await self.pool.execute(
            """
            INSERT INTO bot_users
                (user_id, username, full_name, language_code, first_seen_at, last_seen_at)
            VALUES ($1, $2, $3, $4, NOW(), NOW())
            ON CONFLICT (user_id) DO UPDATE SET
              username = EXCLUDED.username,
              full_name = EXCLUDED.full_name,
              language_code = EXCLUDED.language_code,
              last_seen_at = NOW()
            """,
            user_id, username, full_name, language_code,
        )

    async def is_user_blocked(self, user_id: int) -> bool:
        row = await self.pool.fetchrow(
            "SELECT is_blocked FROM bot_users WHERE user_id=$1",
            user_id,
        )
        return bool(row and row["is_blocked"])

    async def mark_deleted(
        self, connection_id: str, chat_id: int, message_ids: Iterable[int]
    ) -> None:
        ids = list(message_ids)
        if not ids:
            return
        await self.pool.execute(
            """
            UPDATE messages SET deleted_at=NOW()
            WHERE business_connection_id=$1 AND chat_id=$2 AND message_id = ANY($3::bigint[])
            """,
            connection_id, chat_id, ids,
        )
