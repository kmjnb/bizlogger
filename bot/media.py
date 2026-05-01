from __future__ import annotations

from pathlib import Path

from aiogram import Bot
from aiogram.types import Message

# Telegram's send_video / send_animation / send_video_note all expect MP4.
# iPhone .MOV uses the same H.264+AAC stream — just renaming the file works.
_FORCED_SUFFIX = {
    "video": ".mp4",
    "video_note": ".mp4",
    "animation": ".mp4",
    "voice": ".ogg",
    "photo": ".jpg",
}


def detect_media(msg: Message) -> tuple[str | None, str | None]:
    """Return (media_type, file_id) for the largest available media attachment, or (None, None)."""
    if msg.photo:
        return "photo", msg.photo[-1].file_id
    if msg.video:
        return "video", msg.video.file_id
    if msg.video_note:
        return "video_note", msg.video_note.file_id
    if msg.voice:
        return "voice", msg.voice.file_id
    if msg.audio:
        return "audio", msg.audio.file_id
    if msg.animation:
        return "animation", msg.animation.file_id
    if msg.document:
        return "document", msg.document.file_id
    if msg.sticker:
        return "sticker", msg.sticker.file_id
    return None, None


def detect_media_meta(msg: Message) -> tuple[int | None, int | None, int | None]:
    """Return (width, height, duration) for the message's media, when available."""
    if msg.video:
        return msg.video.width, msg.video.height, msg.video.duration
    if msg.video_note:
        return msg.video_note.length, msg.video_note.length, msg.video_note.duration
    if msg.animation:
        return msg.animation.width, msg.animation.height, msg.animation.duration
    if msg.voice:
        return None, None, msg.voice.duration
    if msg.audio:
        return None, None, msg.audio.duration
    if msg.photo:
        p = msg.photo[-1]
        return p.width, p.height, None
    return None, None, None


async def download_to(
    bot: Bot, file_id: str, target_dir: Path, base_name: str, media_type: str | None = None
) -> Path | None:
    """Download a Telegram file by file_id. Returns the local path or None on failure.
    Uses a Telegram-friendly extension based on media_type so re-upload works as native media."""
    try:
        file = await bot.get_file(file_id)
    except Exception:
        return None
    forced = _FORCED_SUFFIX.get(media_type or "")
    suffix = forced or Path(file.file_path or "").suffix or ".bin"
    target_dir.mkdir(parents=True, exist_ok=True)
    out = target_dir / f"{base_name}{suffix}"
    try:
        await bot.download_file(file.file_path, destination=out)
    except Exception:
        return None
    return out
