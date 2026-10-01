"""Фильтры доступа."""
from aiogram.filters import BaseFilter
from aiogram.types import Message

from bot.config.settings import get_config


class IsAdmin(BaseFilter):
    """Владелец + администраторы из БД (кэшируется на время проверки)."""

    async def __call__(self, message: Message) -> bool:
        return await is_admin_id(message.from_user.id if message.from_user else None)


class AdminCallback(BaseFilter):
    pass


async def is_admin_id(tg_id: int | None) -> bool:
    if tg_id is None:
        return False
    cfg = get_config()
    if tg_id == cfg.OWNER_ID:
        return True
    # динамическая проверка по таблице admins-настройки
    from bot.db.base import get_session_factory
    from sqlalchemy import select
    from bot.db.models import Setting
    async with get_session_factory()() as s:
        st = await s.get(Setting, "admin_ids")
        if st and str(tg_id) in st.value.replace(" ", "").split(","):
            return True
    return False
