from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from html import escape

from aiogram import Bot, F, Router
from aiogram.filters import CommandStart
from aiogram.types import (
    BusinessConnection,
    BusinessMessagesDeleted,
    CallbackQuery,
    Chat,
    FSInputFile,
    Message,
)

from config import ADMIN_CHAT_ID, MEDIA_DIR
from . import access
from .db import Db, StoredMessage
from .media import detect_media, detect_media_meta, download_to

_MEDIA_LABEL = {
    "photo": "📷 фото",
    "video": "🎬 видео",
    "video_note": "⚪ кружок",
    "voice": "🎙 голосовое",
    "audio": "🎵 аудио",
    "animation": "🎞 gif",
    "sticker": "💟 стикер",
    "document": "📎 документ",
}


def _chat_label(chat: Chat) -> str:
    if chat.title:
        return chat.title
    parts: list[str] = []
    if chat.first_name:
        parts.append(chat.first_name)
    if chat.last_name:
        parts.append(chat.last_name)
    label = " ".join(parts).strip()
    if chat.username:
        label = f"{label} @{chat.username}".strip() if label else f"@{chat.username}"
    return label or f"id:{chat.id}"


def _fmt_time(unix_ts: int | None) -> str:
    if not unix_ts:
        unix_ts = int(time.time())
    return datetime.fromtimestamp(unix_ts, tz=timezone.utc).astimezone().strftime("%H:%M:%S")


def _media_tag(media_type: str | None) -> str:
    return _MEDIA_LABEL.get(media_type or "", "📎 файл" if media_type else "")


def _quote_block(text: str) -> str:
    return f"<blockquote expandable>{escape(text)}</blockquote>" if text else ""

log = logging.getLogger("bizlogger")

router = Router()
_db: Db | None = None
_download_tasks: dict[tuple[str, int, int], asyncio.Task] = {}


def setup(db: Db) -> Router:
    global _db
    _db = db
    return router


async def _notify_target(conn_id: str) -> int:
    """Resolve the recipient for business-connection notifications.
    Sends to the owner of the connection; falls back to ADMIN_CHAT_ID only
    when the connection row hasn't landed yet (transient race)."""
    assert _db is not None
    owner = await _db.get_owner_id(conn_id)
    if owner is None:
        log.warning("no owner found for conn=%s, falling back to ADMIN_CHAT_ID", conn_id)
        return ADMIN_CHAT_ID
    return owner


async def _send_media_native(
    bot: Bot, chat_id: int, media_type: str, path: str,
    width: int | None = None, height: int | None = None, duration: int | None = None,
) -> None:
    """Send the file with the correct send_* method so videos look like videos, photos like photos, etc."""
    f = FSInputFile(path)
    if media_type == "photo":
        await bot.send_photo(chat_id, f)
    elif media_type == "video":
        await bot.send_video(
            chat_id, f, width=width, height=height, duration=duration, supports_streaming=True,
        )
    elif media_type == "video_note":
        await bot.send_video_note(chat_id, f, length=width, duration=duration)
    elif media_type == "voice":
        await bot.send_voice(chat_id, f, duration=duration)
    elif media_type == "audio":
        await bot.send_audio(chat_id, f, duration=duration)
    elif media_type == "animation":
        await bot.send_animation(chat_id, f, width=width, height=height, duration=duration)
    elif media_type == "sticker":
        await bot.send_sticker(chat_id, f)
    else:
        await bot.send_document(chat_id, f)


async def _download_and_persist(
    bot: Bot, conn_id: str, chat_id: int, message_id: int, file_id: str,
    media_type: str | None = None,
) -> str | None:
    base = f"{conn_id}_{chat_id}_{message_id}"
    downloaded = await download_to(bot, file_id, MEDIA_DIR, base, media_type=media_type)
    if not downloaded:
        log.warning("download failed: conn=%s mid=%s file_id=%s", conn_id, message_id, file_id)
        return None
    assert _db is not None
    await _db.update_local_path(conn_id, chat_id, message_id, str(downloaded))
    return str(downloaded)


def _sender(msg: Message) -> tuple[int | None, str | None]:
    u = msg.from_user
    if not u:
        return None, None
    name = u.full_name or (u.username and f"@{u.username}") or str(u.id)
    return u.id, name


async def _track_dm_sender(msg: Message) -> bool:
    """Upsert the DM sender into bot_users. Returns False if the user is blocked
    (in which case the caller should drop the message without further side effects)."""
    assert _db is not None
    u = msg.from_user
    if u is None or u.is_bot:
        return False
    if await _db.is_user_blocked(u.id):
        return False
    await _db.track_bot_user(
        user_id=u.id,
        username=u.username,
        full_name=u.full_name or None,
        language_code=u.language_code,
    )
    return True


@router.message(CommandStart(), F.chat.type == "private")
async def on_start(msg: Message, bot: Bot) -> None:
    if not await _track_dm_sender(msg):
        return
    assert _db is not None
    user_id = msg.from_user.id if msg.from_user else None
    if user_id is not None:
        gates = await access.missing_gates(bot, _db, user_id)
        if gates:
            text, kb = access.gate_message(gates)
            await bot.send_message(msg.chat.id, text, reply_markup=kb)
            return
    name = msg.from_user.first_name if msg.from_user else "👋"
    await bot.send_message(
        msg.chat.id,
        f"Привет, {escape(name)}! Я бот-логгер для Telegram Business — фиксирую "
        f"сообщения в подключённых business-чатах, в том числе изменения, удаления "
        f"и одноразовое медиа.\n\n"
        f"Чтобы я начал работать, подключи меня в настройках Business у себя в "
        f"Telegram (Настройки → Business → Чат-боты).\n\n"
        f"Тарифы и оплата — /plans",
    )


@router.callback_query(F.data == "gate:recheck")
async def on_gate_recheck(cb: CallbackQuery, bot: Bot) -> None:
    assert _db is not None
    if cb.from_user is None:
        await cb.answer()
        return
    gates = await access.missing_gates(bot, _db, cb.from_user.id)
    if gates:
        text, kb = access.gate_message(gates)
        await cb.answer("Ещё не на всех каналах", show_alert=False)
        try:
            await cb.message.edit_text(text, reply_markup=kb)
        except Exception:
            pass
        return
    await cb.answer("Готово ✅", show_alert=False)
    try:
        await cb.message.edit_text(
            "✅ Доступ открыт. Подключи меня в Business настройках Telegram, "
            "чтобы я начал логировать. Тарифы — /plans"
        )
    except Exception:
        pass


@router.message(F.chat.type == "private")
async def on_private_message(msg: Message) -> None:
    """Track everyone who DMs the bot in private — populates bot_users for the admin."""
    await _track_dm_sender(msg)


@router.business_connection()
async def on_business_connection(conn: BusinessConnection, bot: Bot) -> None:
    assert _db is not None
    await _db.upsert_connection(
        connection_id=conn.id,
        user_id=conn.user.id,
        is_enabled=conn.is_enabled,
        can_reply=conn.can_reply,
    )
    state = "подключён ✅" if conn.is_enabled else "отключён ❌"
    user_label = conn.user.full_name + (f" @{conn.user.username}" if conn.user.username else "")
    body = (
        f"🔗 <b>Бизнес-аккаунт {state}</b>\n"
        f"\n"
        f"👤 <b>Owner:</b> {escape(user_label)} (<code>{conn.user.id}</code>)\n"
        f"🆔 <b>Connection:</b> <code>{escape(conn.id)}</code>\n"
        f"↩️ <b>Can reply:</b> {'да' if conn.can_reply else 'нет'}"
    )
    await bot.send_message(conn.user.id, body)


@router.business_message()
async def on_business_message(msg: Message, bot: Bot) -> None:
    assert _db is not None
    conn_id = msg.business_connection_id or ""
    media_type, file_id = detect_media(msg)
    width, height, duration = detect_media_meta(msg)
    from_id, from_name = _sender(msg)
    text = msg.text or msg.caption
    is_self_destruct = bool(getattr(msg, "has_media_spoiler", False))

    # Save metadata first — fast deletes can fire before media download finishes.
    await _db.save_message(StoredMessage(
        business_connection_id=conn_id,
        chat_id=msg.chat.id,
        message_id=msg.message_id,
        from_id=from_id,
        from_name=from_name,
        text=text,
        media_type=media_type,
        file_id=file_id,
        local_path=None,
        is_self_destruct=is_self_destruct,
        created_at=int(msg.date.timestamp()) if msg.date else int(time.time()),
        edited_at=None,
        deleted_at=None,
        edit_history=None,
        width=width,
        height=height,
        duration=duration,
        chat_label=_chat_label(msg.chat),
        captured_via_reply=False,
    ))

    log.info(
        "business_message conn=%s chat=%s mid=%s from=%s media=%s spoiler=%s text_len=%s",
        conn_id, msg.chat.id, msg.message_id, from_name,
        media_type, is_self_destruct, len(text or ""),
    )
    # Dump full payload for unknown / interesting cases so we can diagnose view-once etc.
    if is_self_destruct or (file_id is None and text is None):
        try:
            log.info("payload: %s", msg.model_dump_json(exclude_none=True))
        except Exception:
            pass

    if file_id:
        key = (conn_id, msg.chat.id, msg.message_id)
        task = asyncio.create_task(
            _download_and_persist(bot, conn_id, msg.chat.id, msg.message_id, file_id, media_type)
        )
        _download_tasks[key] = task
        task.add_done_callback(lambda _t, k=key: _download_tasks.pop(k, None))

    # Capture-via-reply: if this message replies to something we've never seen,
    # the replied content (incl. view-once / self-destruct media) lives in reply_to_message.
    if msg.reply_to_message:
        await _capture_replied(bot, conn_id, msg.chat.id, msg.reply_to_message)


async def _capture_replied(bot: Bot, conn_id: str, chat_id: int, replied: Message) -> None:
    """Save a message we observed only via someone else's reply_to_message."""
    assert _db is not None
    existing = await _db.get_message(conn_id, chat_id, replied.message_id)
    if existing and (existing.local_path or existing.text):
        return

    media_type, file_id = detect_media(replied)
    width, height, duration = detect_media_meta(replied)
    text = replied.text or replied.caption
    if not media_type and not text:
        return

    from_user = replied.from_user
    from_id = from_user.id if from_user else None
    from_name = (
        from_user.full_name if from_user
        else (replied.sender_chat.title if replied.sender_chat else None)
    )
    is_self_destruct = bool(getattr(replied, "has_media_spoiler", False))

    await _db.save_message(StoredMessage(
        business_connection_id=conn_id,
        chat_id=chat_id,
        message_id=replied.message_id,
        from_id=from_id,
        from_name=from_name,
        text=text,
        media_type=media_type,
        file_id=file_id,
        local_path=None,
        is_self_destruct=is_self_destruct,
        created_at=int(replied.date.timestamp()) if replied.date else int(time.time()),
        edited_at=None,
        deleted_at=None,
        edit_history=None,
        width=width,
        height=height,
        duration=duration,
        chat_label=_chat_label(replied.chat) if replied.chat else None,
        captured_via_reply=True,
    ))
    log.info(
        "captured via reply: conn=%s chat=%s mid=%s media=%s from=%s",
        conn_id, chat_id, replied.message_id, media_type, from_name,
    )

    local_path: str | None = None
    if file_id:
        local_path = await _download_and_persist(
            bot, conn_id, chat_id, replied.message_id, file_id, media_type,
        )

    when = _fmt_time(int(replied.date.timestamp()) if replied.date else None)
    media_tag = _media_tag(media_type)
    header = (
        f"👁 <b>Захвачено через reply</b>  <i>{when}</i>\n"
        f"💬 <b>Чат:</b> {escape(_chat_label(replied.chat) if replied.chat else '—')}\n"
        f"👤 <b>От:</b> {escape(from_name or '—')}"
    )
    if media_tag:
        header += f"\n📦 <b>Тип:</b> {media_tag}"
    if text:
        header += f"\n\n{_quote_block(text)}"
    target = await _notify_target(conn_id)
    await bot.send_message(target, header)
    if local_path and media_type:
        try:
            await _send_media_native(
                bot, target, media_type, local_path,
                width=width, height=height, duration=duration,
            )
        except Exception as e:
            log.warning("send captured media failed: %s", e)


@router.edited_business_message()
async def on_business_edited(msg: Message, bot: Bot) -> None:
    assert _db is not None
    conn_id = msg.business_connection_id or ""
    new_text = msg.text or msg.caption or ""
    prev = await _db.get_message(conn_id, msg.chat.id, msg.message_id)
    old_text = (prev.text if prev else "") or ""

    history = (prev.edit_history + "\n---\n" if prev and prev.edit_history else "") + old_text
    await _db.mark_edited(conn_id, msg.chat.id, msg.message_id, new_text, history)

    # Skip notifying when the owner edits his own message.
    owner_id = await _db.get_owner_id(conn_id)
    editor_id = msg.from_user.id if msg.from_user else None
    if owner_id is not None and editor_id == owner_id:
        return

    who = (prev and prev.from_name) or "—"
    chat_lbl = _chat_label(msg.chat)
    when = _fmt_time(int(msg.edit_date.timestamp()) if msg.edit_date else None)
    report = (
        f"✏️ <b>Сообщение изменено</b>  <i>{when}</i>\n"
        f"💬 <b>Чат:</b> {escape(chat_lbl)}\n"
        f"👤 <b>От:</b> {escape(who)}\n"
        f"\n"
        f"<b>Было:</b>\n{_quote_block(old_text)}"
        f"<b>Стало:</b>\n{_quote_block(new_text)}"
    )
    target = owner_id if owner_id is not None else ADMIN_CHAT_ID
    if owner_id is None:
        log.warning("no owner found for conn=%s, falling back to ADMIN_CHAT_ID", conn_id)
    await bot.send_message(target, report)


@router.deleted_business_messages()
async def on_business_deleted(event: BusinessMessagesDeleted, bot: Bot) -> None:
    assert _db is not None
    conn_id = event.business_connection_id
    chat_id = event.chat.id
    chat_lbl = _chat_label(event.chat)
    owner_id = await _db.get_owner_id(conn_id)
    target = owner_id if owner_id is not None else ADMIN_CHAT_ID
    if owner_id is None:
        log.warning("no owner found for conn=%s, falling back to ADMIN_CHAT_ID", conn_id)

    for mid in event.message_ids:
        # If the insert is racing this delete, give it a moment to land and let any
        # in-flight media download finish before we fall back.
        key = (conn_id, chat_id, mid)
        in_flight = _download_tasks.get(key)
        if in_flight is not None:
            try:
                await asyncio.wait_for(in_flight, timeout=8.0)
            except (asyncio.TimeoutError, Exception) as e:
                log.warning("waited for in-flight download mid=%s: %s", mid, e)

        prev = await _db.get_message(conn_id, chat_id, mid)
        who = (prev and prev.from_name) or "—"
        when = _fmt_time(None)
        if prev is None:
            await bot.send_message(
                target,
                f"🗑 <b>Сообщение удалено</b>  <i>{when}</i>\n"
                f"💬 <b>Чат:</b> {escape(chat_lbl)}\n"
                f"⚠️ Контент не сохранён (msg_id <code>{mid}</code> вне кэша)",
            )
            continue

        # Skip notifying when the deleted message was authored by the owner himself.
        if owner_id is not None and prev.from_id == owner_id:
            log.info("skip notify: owner deleted own message mid=%s", mid)
            continue

        # Fallback: row exists but media wasn't downloaded yet — try right now.
        if prev.file_id and not prev.local_path:
            saved = await _download_and_persist(
                bot, conn_id, chat_id, mid, prev.file_id, prev.media_type,
            )
            if saved:
                prev = await _db.get_message(conn_id, chat_id, mid) or prev

        text = prev.text or ""
        media_tag = _media_tag(prev.media_type)
        header = (
            f"🗑 <b>Сообщение удалено</b>  <i>{when}</i>\n"
            f"💬 <b>Чат:</b> {escape(chat_lbl)}\n"
            f"👤 <b>От:</b> {escape(who)}"
        )
        if media_tag:
            header += f"\n📦 <b>Тип:</b> {media_tag}"
        if text:
            header += f"\n\n{_quote_block(text)}"
        await bot.send_message(target, header)
        if prev.local_path and prev.media_type:
            try:
                await _send_media_native(
                    bot, target, prev.media_type, prev.local_path,
                    width=prev.width, height=prev.height, duration=prev.duration,
                )
            except Exception as e:
                log.warning("send media failed (%s, %s): %s", prev.media_type, prev.local_path, e)
                await bot.send_message(
                    target,
                    f"(media не отправился: {escape(str(e))}, путь: <code>{escape(prev.local_path)}</code>)",
                )

    await _db.mark_deleted(conn_id, chat_id, event.message_ids)
