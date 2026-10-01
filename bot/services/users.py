"""Сервис пользователей и настроек."""
from __future__ import annotations

import re

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.base import get_setting
from bot.db.models import Deal, Review, Setting, User

NICK_RE = re.compile(r"^[A-Za-z0-9_]{3,20}$")


def valid_nickname(nick: str) -> bool:
    return bool(NICK_RE.match(nick or ""))


async def get_user(session: AsyncSession, tg_id: int) -> User | None:
    return (await session.execute(select(User).where(User.tg_id == tg_id))).scalar_one_or_none()


async def get_user_by_id(session: AsyncSession, user_id: int) -> User | None:
    return await session.get(User, user_id)


async def ensure_user(session: AsyncSession, tg_id: int, username: str | None = None,
                      first_name: str | None = None) -> User:
    user = await get_user(session, tg_id)
    if user is None:
        user = User(tg_id=tg_id, username=username, first_name=first_name)
        session.add(user)
        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()
            user = await get_user(session, tg_id)
    else:
        changed = False
        if username and user.username != username:
            user.username, changed = username, True
        if first_name and user.first_name != first_name:
            user.first_name, changed = first_name, True
        if changed:
            await session.commit()
    return user


async def set_nickname(session: AsyncSession, user: User, nickname: str) -> bool:
    """Уникальность гарантируется UNIQUE-индексом + предварительной проверкой."""
    if not valid_nickname(nickname):
        return False
    taken = (await session.execute(
        select(User.id).where(User.nickname == nickname, User.id != user.id)
    )).scalar_one_or_none()
    if taken is not None:
        return False
    user.nickname = nickname
    try:
        await session.commit()
        return True
    except IntegrityError:
        await session.rollback()
        return False


async def complete_registration(session: AsyncSession, user: User) -> None:
    user.registered = True
    await session.commit()


async def recalc_rating(session: AsyncSession, target_id: int) -> None:
    cnt = (await session.execute(
        select(func.count(Review.id), func.coalesce(func.sum(Review.stars), 0))
        .where(Review.target_id == target_id)
    )).one()
    user = await session.get(User, target_id)
    if user:
        user.reviews_count = cnt[0]
        user.rating_sum = int(cnt[1])
        await session.commit()


async def inc_deals_completed(session: AsyncSession, user_id: int) -> None:
    user = await session.get(User, user_id)
    if user:
        user.deals_completed += 1
        await session.commit()


async def is_maintenance(session: AsyncSession) -> bool:
    return (await get_setting(session, "maintenance", "0")) == "1"


async def settings_map(session: AsyncSession) -> dict[str, str]:
    rows = (await session.execute(select(Setting))).scalars().all()
    return {r.key: r.value for r in rows}
