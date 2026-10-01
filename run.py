"""Playerok — точка входа.

Запуск:
    BOT_TOKEN=... DATABASE_URL=... python run.py
Либо через .env в корне проекта.
"""
from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.base import DefaultKeyBuilder
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand

from bot.config.settings import get_config
from bot.db.base import init_db
from bot.middlewares import (
    AccessCheckMiddleware,
    DbSessionMiddleware,
    ErrorAnswerMiddleware,
    ThrottleCallbackMiddleware,
)
from bot.handlers import common, deal_create, deals_list, disputes_reviews
from bot.handlers import registration, wallet
from bot.admin import broadcast, catalog_settings, finance, main as admin_main

log = logging.getLogger("playerok")


def build_storage():
    cfg = get_config()
    if cfg.REDIS_URL:
        try:
            from redis.asyncio import Redis
            from aiogram.fsm.storage.redis import RedisStorage

            return RedisStorage(redis=Redis.from_url(cfg.REDIS_URL))
        except Exception as e:  # pragma: no cover - деградация осознанная
            log.warning("Redis недоступен (%s), использую MemoryStorage", e)
    return MemoryStorage()


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    cfg = get_config()
    if not cfg.BOT_TOKEN:
        raise SystemExit(
            "❌ BOT_TOKEN не задан. Укажите его в переменных окружения или в файле .env"
        )

    await init_db()

    bot = Bot(
        token=cfg.BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    me = await bot.get_me()
    # кэшируем username для deep link'ов сделок
    setattr(bot, "_playerok_username", me.username or "")
    log.info("Бот @%s (id=%s) запущен", me.username, me.id)

    dp = Dispatcher(
        storage=build_storage(),
        # изолируем ключи FSM по пользователям и чатам (важно при группах/нескольких чатах)
        key_builder=DefaultKeyBuilder(with_destiny=True),
    )
    dp["bot_username"] = me.username or ""

    # Мидлвари: порядок важен — session первой, throttle до хендлеров,
    # access-check после session (нужен доступ к БД).
    dp.message.middleware(DbSessionMiddleware())
    dp.callback_query.middleware(DbSessionMiddleware())
    dp.callback_query.middleware(ThrottleCallbackMiddleware())
    dp.message.middleware(AccessCheckMiddleware())
    dp.callback_query.middleware(AccessCheckMiddleware())
    dp.errors.middleware(ErrorAnswerMiddleware())

    # Роутеры пользователя
    dp.include_router(registration.router)
    dp.include_router(deals_list.router)      # /start deal_CODE + карточки + оплата
    dp.include_router(deal_create.router)
    dp.include_router(wallet.router)
    dp.include_router(disputes_reviews.router)
    dp.include_router(common.router)
    # Админ-роутеры
    dp.include_router(admin_main.router)
    dp.include_router(finance.router)
    dp.include_router(catalog_settings.router)
    dp.include_router(broadcast.router)

    await bot.set_my_commands([
        BotCommand(command="start", description="Начать / регистрация"),
        BotCommand(command="menu", description="Главное меню"),
        BotCommand(command="deal", description="Открыть сделку по коду"),
        BotCommand(command="admin", description="Админ-панель"),
    ])

    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        log.info("Бот остановлен")
