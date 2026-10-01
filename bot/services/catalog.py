"""Сервисы отзывов, категорий, баннеров, статистики, рассылки и логов админа."""
from __future__ import annotations

import logging
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import (
    AdminAction, Banner, Category, Deal, Game, NewsPost, Payment, Review,
    Subcategory, Transaction, User, Withdrawal,
)
from bot.services.users import recalc_rating

log = logging.getLogger("playerok.catalog")


# ---------- Отзывы ----------

async def can_review(session: AsyncSession, deal: Deal, user: User) -> bool:
    if deal.status != "COMPLETED":
        return False
    if user.id not in (deal.seller_id, deal.buyer_id):
        return False
    existing = (await session.execute(
        select(Review.id).where(Review.deal_id == deal.id, Review.author_id == user.id)
    )).scalar_one_or_none()
    return existing is None


async def add_review(session: AsyncSession, *, deal: Deal, author: User,
                     stars: int, text: str | None) -> bool:
    if not (1 <= stars <= 5):
        return False
    target_id = deal.buyer_id if author.id == deal.seller_id else deal.seller_id
    if target_id is None:
        return False
    r = Review(deal_id=deal.id, author_id=author.id, target_id=target_id,
               stars=stars, text=(text or "").strip()[:500] or None)
    session.add(r)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        return False  # один пользователь — один отзыв на сделку
    await recalc_rating(session, target_id)
    return True


async def user_reviews(session: AsyncSession, target_id: int):
    stmt = select(Review).where(Review.target_id == target_id).order_by(Review.created_at.desc())
    return (await session.execute(stmt)).scalars().all()


# ---------- Категории / игры ----------

async def active_categories(session: AsyncSession):
    stmt = select(Category).where(Category.active.is_(True)).order_by(Category.sort_order.asc())
    return (await session.execute(stmt)).scalars().all()


async def all_categories(session: AsyncSession):
    stmt = select(Category).order_by(Category.sort_order.asc())
    return (await session.execute(stmt)).scalars().all()


async def category_games(session: AsyncSession, category_id: int):
    stmt = select(Game).where(Game.category_id == category_id, Game.active.is_(True)) \
        .order_by(Game.sort_order.asc())
    return (await session.execute(stmt)).scalars().all()


async def add_category(session: AsyncSession, name: str, emoji: str = "📦",
                       requires_game: bool = False) -> Category | None:
    name = name.strip()[:120]
    if not name:
        return None
    exists = (await session.execute(select(Category).where(Category.name == name))) \
        .scalar_one_or_none()
    if exists:
        return None
    maxo = (await session.execute(select(func.coalesce(func.max(Category.sort_order), -1)))) \
        .scalar_one()
    cat = Category(name=name, emoji=emoji[:8] or "📦", sort_order=maxo + 1,
                   active=True, requires_game=requires_game)
    session.add(cat)
    await session.commit()
    await session.refresh(cat)
    return cat


async def add_game(session: AsyncSession, category_id: int, name: str) -> Game | None:
    name = name.strip()[:120]
    if not name:
        return None
    maxo = (await session.execute(
        select(func.coalesce(func.max(Game.sort_order), -1)).where(Game.category_id == category_id)
    )).scalar_one()
    g = Game(category_id=category_id, name=name, sort_order=maxo + 1, active=True)
    session.add(g)
    await session.commit()
    return g


async def set_category_active(session: AsyncSession, cat_id: int, active: bool) -> None:
    cat = await session.get(Category, cat_id)
    if cat:
        cat.active = active
        await session.commit()


async def rename_category(session: AsyncSession, cat_id: int, new_name: str) -> bool:
    cat = await session.get(Category, cat_id)
    if not cat:
        return False
    cat.name = new_name.strip()[:120]
    try:
        await session.commit()
        return True
    except IntegrityError:
        await session.rollback()
        return False


async def delete_category(session: AsyncSession, cat_id: int) -> None:
    cat = await session.get(Category, cat_id)
    if cat:
        await session.delete(cat)
        await session.commit()


async def move_category(session: AsyncSession, cat_id: int, direction: int) -> None:
    cats = list((await all_categories(session)))
    idx = next((i for i, c in enumerate(cats) if c.id == cat_id), None)
    if idx is None:
        return
    j = idx + direction
    if 0 <= j < len(cats):
        cats[idx].sort_order, cats[j].sort_order = j, idx
        await session.commit()


# ---------- Баннеры ----------

async def get_banner(session: AsyncSession, section: str) -> str | None:
    b = await session.get(Banner, section)
    if b and b.file_id:
        return b.file_id
    return None


async def set_banner(session: AsyncSession, section: str, file_id: str) -> None:
    b = await session.get(Banner, section)
    if b is None:
        session.add(Banner(section=section, file_id=file_id))
    else:
        b.file_id = file_id
    await session.commit()


# ---------- Новости ----------

async def published_news(session: AsyncSession, limit: int = 10):
    stmt = select(NewsPost).where(NewsPost.published.is_(True)) \
        .order_by(NewsPost.created_at.desc()).limit(limit)
    return (await session.execute(stmt)).scalars().all()


# ---------- Статистика ----------

async def stats(session: AsyncSession) -> dict:
    import datetime as dt
    from sqlalchemy import or_

    from bot.db.models import Dispute

    bind_name = session.get_bind().dialect.name
    day_start = dt.datetime.now(dt.timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0)

    users_total = (await session.execute(select(func.count(User.id)))).scalar_one()
    if bind_name == "sqlite":
        # SQLite хранит naive-даты — сравниваем со сдвигом
        users_today = (await session.execute(
            select(func.count(User.id)).where(User.created_at >= day_start.replace(tzinfo=None))
        )).scalar_one()
    else:
        users_today = (await session.execute(
            select(func.count(User.id)).where(User.created_at >= day_start)
        )).scalar_one()
    deals_active = (await session.execute(
        select(func.count(Deal.id)).where(Deal.status.notin_(
            ["COMPLETED", "CANCELLED", "REJECTED", "REFUNDED"])))).scalar_one()
    deals_completed = (await session.execute(
        select(func.count(Deal.id)).where(Deal.status == "COMPLETED"))).scalar_one()
    total_turnover = (await session.execute(
        select(func.coalesce(func.sum(Deal.price), 0)).where(Deal.status == "COMPLETED"))) \
        .scalar_one()
    total_commission = (await session.execute(
        select(func.coalesce(func.sum(Deal.commission), 0)).where(Deal.status == "COMPLETED"))) \
        .scalar_one()
    pend_payments = (await session.execute(
        select(func.count(Payment.id)).where(Payment.status == "PENDING"))).scalar_one()
    pend_withdraws = (await session.execute(
        select(func.count(Withdrawal.id)).where(Withdrawal.status.in_(["PENDING", "APPROVED"])))
    ).scalar_one()
    open_disputes = (await session.execute(
        select(func.count(Dispute.id)).where(Dispute.status == "OPEN"))).scalar_one()
    bal_sum = (await session.execute(
        select(func.coalesce(func.sum(User.available_balance), 0)))).scalar_one()
    frozen_sum = (await session.execute(
        select(func.coalesce(func.sum(User.frozen_balance), 0)))).scalar_one()
    return {
        "users_total": users_total, "users_today": users_today,
        "deals_active": deals_active, "deals_completed": deals_completed,
        "turnover": Decimal(str(total_turnover)), "commission": Decimal(str(total_commission)),
        "pend_payments": pend_payments, "pend_withdraws": pend_withdraws,
        "open_disputes": open_disputes,
        "balance_sum": Decimal(str(bal_sum)), "frozen_sum": Decimal(str(frozen_sum)),
    }


# ---------- Поиск пользователей ----------

async def search_users(session: AsyncSession, query: str):
    from sqlalchemy import or_
    q = query.strip()
    conds = []
    if q.isdigit():
        conds.append(User.tg_id == int(q))
        conds.append(User.id == int(q))
    like = f"%{q}%"
    conds.append(User.username.ilike(like))
    conds.append(User.nickname.ilike(like))
    stmt = select(User).where(or_(*conds)).limit(25)
    return (await session.execute(stmt)).scalars().all()


# ---------- Логи админ-действий ----------

async def log_admin_action(session: AsyncSession, admin_id: int, action: str, obj: str,
                           old_value: str | None = None, new_value: str | None = None) -> None:
    session.add(AdminAction(admin_id=admin_id, action=action, object=obj,
                            old_value=old_value, new_value=new_value))
    await session.commit()
