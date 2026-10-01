"""Мидлвари: БД-сессия, защита от блокировок/техработ, антифлуд callback'ов."""
from __future__ import annotations

import logging
import time
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

from bot.db.base import get_session_factory
from bot.db.models import User
from bot.filters.access import is_admin_id
from bot.utils.texts import err_text, status_line

log = logging.getLogger("playerok.middleware")

# простой in-memory антифлуд для callback'ов (защита от двойных нажатий)
_cb_locks: dict[tuple[int, str], float] = {}
CB_LOCK_TTL = 2.5


def _prune(now: float):
    if len(_cb_locks) > 4096:
        for k, v in list(_cb_locks.items()):
            if now - v > CB_LOCK_TTL:
                _cb_locks.pop(k, None)


class DbSessionMiddleware(BaseMiddleware):
    async def __call__(self, handler, event: TelegramObject, data: dict):
        async with get_session_factory()() as session:
            data["session"] = session
            return await handler(event, data)


class ThrottleCallbackMiddleware(BaseMiddleware):
    """Не даёт выполнить один и тот же callback дважды за короткое окно."""
    async def __call__(self, handler, event: TelegramObject, data: dict):
        if isinstance(event, CallbackQuery):
            key = (event.from_user.id, event.data or "")
            now = time.monotonic()
            if key in _cb_locks and now - _cb_locks[key] < CB_LOCK_TTL:
                try:
                    await event.answer("⏳ Уже обрабатывается…", show_alert=False)
                except Exception:
                    pass
                return True
            _cb_locks[key] = now
            _prune(now)
        return await handler(event, data)


class AccessCheckMiddleware(BaseMiddleware):
    """Блокировка забаненных пользователей; режим техработ для не-админов."""
    async def __call__(self, handler, event: TelegramObject, data: dict):
        user = None
        tg_id = None
        if isinstance(event, (Message, CallbackQuery)) and event.from_user:
            tg_id = event.from_user.id
        if tg_id is None:
            return await handler(event, data)

        session = data.get("session")
        admin = await is_admin_id(tg_id)
        data["is_admin"] = admin
        if admin:
            return await handler(event, data)

        if session is not None:
            from sqlalchemy import select
            db_user = (await session.execute(
                select(User).where(User.tg_id == tg_id))).scalar_one_or_none()
            data["db_user"] = db_user
            if db_user and db_user.blocked:
                text = "🚫 <b>ВАШ АККАУНТ ЗАБЛОКИРОВАН</b>\n\n" \
                       f"{err_text('Обратитесь в поддержку для уточнения статуса.')}"
                await self._deny(event, text)
                return True
            from bot.db.base import get_setting
            maintenance = (await get_setting(session, "maintenance", "0")) == "1"
            data["maintenance"] = maintenance
            if maintenance:
                from bot.utils.texts import MAINTENANCE_TEXT
                from bot.keyboard.kbs import inl
                kb = inl([[("🆘 ПОДДЕРЖКА", "support:menu")]])
                await self._deny(event, MAINTENANCE_TEXT, kb)
                return True
        return await handler(event, data)

    async def _deny(self, event, text: str, kb=None):
        try:
            if isinstance(event, CallbackQuery):
                await event.bot.send_message(event.message.chat.id, text,
                                             reply_markup=kb, parse_mode="HTML")
                await event.answer()
            elif isinstance(event, Message):
                await event.answer(text, reply_markup=kb, parse_mode="HTML")
        except Exception:
            log.exception("deny message failed")
