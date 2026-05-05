from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.exceptions import (
    TelegramAPIError,
    TelegramForbiddenError,
    TelegramRetryAfter,
)
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

import asyncpg

log = logging.getLogger("bizlogger.broadcaster")

# Telegram global hard cap is ~30 msg/sec for bot messages to different users.
# Keep margin → ~22-25/sec.
INTER_MESSAGE_DELAY = 0.045
POLL_INTERVAL = 5.0
RECIPIENT_BATCH = 200


class Broadcaster:
    """Background task: drains scheduled/running broadcasts, sends to each recipient."""

    def __init__(self, bot: Bot, pool: asyncpg.Pool):
        self.bot = bot
        self.pool = pool
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run(), name="broadcaster")

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=5)
            except asyncio.TimeoutError:
                self._task.cancel()

    async def _run(self) -> None:
        log.info("Broadcaster started (delay=%.3fs/msg, poll=%ss)", INTER_MESSAGE_DELAY, POLL_INTERVAL)
        while not self._stop.is_set():
            try:
                worked = await self._tick()
            except Exception:
                log.exception("Broadcaster tick crashed")
                worked = False
            try:
                await asyncio.wait_for(
                    self._stop.wait(),
                    timeout=0.5 if worked else POLL_INTERVAL,
                )
            except asyncio.TimeoutError:
                pass
        log.info("Broadcaster stopped")

    async def _tick(self) -> bool:
        # Pick up next active broadcast — atomically flip 'scheduled' to 'running'.
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                UPDATE broadcasts
                   SET status='running',
                       started_at=COALESCE(started_at, NOW())
                 WHERE id = (
                    SELECT id FROM broadcasts
                     WHERE (status='scheduled' AND (scheduled_at IS NULL OR scheduled_at <= NOW()))
                        OR status='running'
                  ORDER BY COALESCE(scheduled_at, created_at)
                     LIMIT 1
                  FOR UPDATE SKIP LOCKED
                 )
                 RETURNING id, title, payload, total_recipients
                """
            )
        if row is None:
            return False
        await self._drain(row["id"], row["title"], row["payload"])
        return True

    async def _drain(self, broadcast_id: int, title: str, payload_raw: Any) -> None:
        payload = _coerce_payload(payload_raw)
        log.info("Working broadcast #%d (%s)", broadcast_id, title)

        while not self._stop.is_set():
            # Re-read status — admin may have cancelled.
            status = await self.pool.fetchval(
                "SELECT status FROM broadcasts WHERE id=$1", broadcast_id
            )
            if status != "running":
                log.info("Broadcast #%d no longer running (status=%s)", broadcast_id, status)
                return

            recipients = await self.pool.fetch(
                """
                SELECT user_id FROM broadcast_recipients
                 WHERE broadcast_id=$1 AND status='pending'
                 ORDER BY user_id
                 LIMIT $2
                """,
                broadcast_id, RECIPIENT_BATCH,
            )
            if not recipients:
                # Done — no more pending.
                await self.pool.execute(
                    """
                    UPDATE broadcasts
                       SET status='done', finished_at=NOW()
                     WHERE id=$1 AND status='running'
                    """,
                    broadcast_id,
                )
                log.info("Broadcast #%d finished", broadcast_id)
                return

            for r in recipients:
                if self._stop.is_set():
                    return
                user_id = int(r["user_id"])
                ok, err = await self._send_one(user_id, payload)
                if ok:
                    await self.pool.execute(
                        """
                        UPDATE broadcast_recipients
                           SET status='sent', sent_at=NOW(), error_message=NULL
                         WHERE broadcast_id=$1 AND user_id=$2
                        """,
                        broadcast_id, user_id,
                    )
                    await self.pool.execute(
                        "UPDATE broadcasts SET sent_count=sent_count+1 WHERE id=$1",
                        broadcast_id,
                    )
                else:
                    blocked = err and ("blocked" in err.lower() or "forbidden" in err.lower() or "deactivated" in err.lower())
                    await self.pool.execute(
                        """
                        UPDATE broadcast_recipients
                           SET status='failed', sent_at=NOW(), error_message=$3
                         WHERE broadcast_id=$1 AND user_id=$2
                        """,
                        broadcast_id, user_id, (err or "unknown")[:500],
                    )
                    await self.pool.execute(
                        "UPDATE broadcasts SET failed_count=failed_count+1 WHERE id=$1",
                        broadcast_id,
                    )
                    if blocked:
                        await self.pool.execute(
                            "UPDATE bot_users SET is_blocked=true WHERE user_id=$1",
                            user_id,
                        )
                await asyncio.sleep(INTER_MESSAGE_DELAY)

    async def _send_one(self, chat_id: int, payload: dict[str, Any]) -> tuple[bool, str | None]:
        """Returns (success, error_message)."""
        text: str | None = payload.get("text")
        parse_mode = payload.get("parseMode")
        media = payload.get("media")
        buttons = payload.get("buttons")

        pm = ParseMode.MARKDOWN_V2 if parse_mode == "markdown_v2" else None
        kb = _build_keyboard(buttons)

        for attempt in range(2):
            try:
                if media:
                    mtype = media.get("type")
                    file_id = media.get("fileId")
                    if not file_id:
                        return False, "media without file_id"
                    caption = (text or None)
                    if caption and len(caption) > 1024:
                        # Caption limit — split: send media without caption, then text.
                        if mtype == "photo":
                            await self.bot.send_photo(chat_id, file_id, reply_markup=kb)
                        elif mtype == "video":
                            await self.bot.send_video(chat_id, file_id, reply_markup=kb)
                        else:
                            await self.bot.send_document(chat_id, file_id, reply_markup=kb)
                        await self.bot.send_message(chat_id, caption, parse_mode=pm)
                    else:
                        if mtype == "photo":
                            await self.bot.send_photo(chat_id, file_id, caption=caption, parse_mode=pm, reply_markup=kb)
                        elif mtype == "video":
                            await self.bot.send_video(chat_id, file_id, caption=caption, parse_mode=pm, reply_markup=kb)
                        else:
                            await self.bot.send_document(chat_id, file_id, caption=caption, parse_mode=pm, reply_markup=kb)
                else:
                    if not text:
                        return False, "empty payload"
                    await self.bot.send_message(chat_id, text, parse_mode=pm, reply_markup=kb)
                return True, None
            except TelegramRetryAfter as e:
                wait = float(e.retry_after) + 0.5
                log.warning("RetryAfter %.1fs for chat=%d (attempt %d)", wait, chat_id, attempt + 1)
                await asyncio.sleep(wait)
                continue
            except TelegramForbiddenError as e:
                return False, f"forbidden: {e.message}"
            except TelegramAPIError as e:
                return False, f"tg: {e.message}"
            except Exception as e:
                return False, f"err: {e}"
        return False, "retry_after exhausted"


def _coerce_payload(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {}
    return {}


def _build_keyboard(buttons: Any) -> InlineKeyboardMarkup | None:
    if not buttons:
        return None
    if not isinstance(buttons, list):
        return None
    rows: list[list[InlineKeyboardButton]] = []
    for row in buttons:
        if not isinstance(row, list):
            continue
        kb_row: list[InlineKeyboardButton] = []
        for b in row:
            if not isinstance(b, dict):
                continue
            text = (b.get("text") or "").strip()
            url = (b.get("url") or "").strip()
            if not text or not url:
                continue
            kb_row.append(InlineKeyboardButton(text=text, url=url))
        if kb_row:
            rows.append(kb_row)
    if not rows:
        return None
    return InlineKeyboardMarkup(inline_keyboard=rows)
