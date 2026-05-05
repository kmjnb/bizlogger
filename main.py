from __future__ import annotations

import asyncio
import logging
import os

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode

from bot.broadcaster import Broadcaster
from bot.db import Db
from bot.handlers import setup
from config import BOT_TOKEN, DATABASE_URL

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("bizlogger")


async def main() -> None:
    db = Db(DATABASE_URL)
    await db.init()

    proxy_url = os.environ.get("BOT_PROXY_URL") or None
    session = AiohttpSession(proxy=proxy_url) if proxy_url else None

    bot = Bot(
        BOT_TOKEN,
        session=session,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    me = await bot.get_me()
    log.info(
        "Logged in as @%s (id=%s, can_join_groups=%s) via %s",
        me.username, me.id, me.can_join_groups,
        proxy_url or "direct",
    )

    dp = Dispatcher()
    dp.include_router(setup(db))

    broadcaster = Broadcaster(bot, db.pool)
    broadcaster.start()

    log.info("Starting long polling…")
    try:
        await dp.start_polling(
            bot,
            allowed_updates=[
                "message",
                "business_connection",
                "business_message",
                "edited_business_message",
                "deleted_business_messages",
            ],
        )
    finally:
        await broadcaster.stop()
        await bot.session.close()
        await db.close()


if __name__ == "__main__":
    asyncio.run(main())
