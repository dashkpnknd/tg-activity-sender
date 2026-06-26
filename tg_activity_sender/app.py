from __future__ import annotations

import asyncio
import logging

from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties

from tg_activity_sender.admin_bot import build_dispatcher
from tg_activity_sender.config import Settings
from tg_activity_sender.db import Database
from tg_activity_sender.telegram_accounts import AccountManager
from tg_activity_sender.worker import CampaignWorker


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    settings = Settings.from_env()
    settings.media_dir.mkdir(parents=True, exist_ok=True)
    settings.session_dir.mkdir(parents=True, exist_ok=True)

    db = Database(settings.database_path)
    db.init()
    account_manager = AccountManager(
        db,
        api_id=settings.telegram_api_id,
        api_hash=settings.telegram_api_hash,
        session_dir=settings.session_dir,
        proxy_url=settings.telegram_proxy_url,
    )
    bot = Bot(settings.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dispatcher = build_dispatcher(
        db,
        account_manager,
        admin_ids=settings.admin_ids,
        media_dir=settings.media_dir,
    )
    worker = CampaignWorker(
        db,
        account_manager,
        timezone=settings.timezone,
        notification_bot=bot,
        notification_chat_id=settings.notification_chat_id,
    )
    worker_task = asyncio.create_task(worker.run_forever())
    try:
        await dispatcher.start_polling(bot)
    finally:
        worker.stop()
        await worker_task
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
