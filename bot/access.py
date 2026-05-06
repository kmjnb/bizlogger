"""Channel-gate enforcement: user must be subscribed to all active channel_gates
before the bot exposes paid functionality. Checked on /start and /buy."""
from __future__ import annotations

import logging
from html import escape

from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from .db import Db

log = logging.getLogger("bizlogger.access")

_OK_STATUSES = {"member", "administrator", "creator"}


async def missing_gates(bot: Bot, db: Db, user_id: int) -> list[dict]:
    """Return the list of active gates the user is NOT a member of.
    Empty list ⇒ user passes the gate (or there are no active gates)."""
    gates = await db.get_active_gates()
    missing: list[dict] = []
    for g in gates:
        try:
            member = await bot.get_chat_member(g["channel_id"], user_id)
        except Exception as e:
            log.warning(
                "get_chat_member failed for channel=%s user=%s: %s",
                g["channel_id"], user_id, e,
            )
            missing.append(g)
            continue
        if member.status not in _OK_STATUSES:
            missing.append(g)
    return missing


def gate_message(missing: list[dict]) -> tuple[str, InlineKeyboardMarkup]:
    """Build the "please subscribe" prompt with one button per missing channel."""
    lines = ["🔒 <b>Чтобы пользоваться ботом, подпишись на каналы:</b>", ""]
    buttons: list[list[InlineKeyboardButton]] = []
    for g in missing:
        username = g.get("channel_username")
        invite = g.get("invite_link")
        url = invite or (f"https://t.me/{username}" if username else None)
        label = f"@{username}" if username else f"id:{g['channel_id']}"
        lines.append(f"• {escape(label)}")
        if url:
            buttons.append([InlineKeyboardButton(text=f"Открыть {label}", url=url)])
    buttons.append([InlineKeyboardButton(text="🔄 Я подписался", callback_data="gate:recheck")])
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=buttons)
