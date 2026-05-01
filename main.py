from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

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

    bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    me = await bot.get_me()
    log.info("Logged in as @%s (id=%s, can_join_groups=%s)", me.username, me.id, me.can_join_groups)

    dp = Dispatcher()
    dp.include_router(setup(db))

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
        await bot.session.close()
        await db.close()


if __name__ == "__main__":
    asyncio.run(main())
