"""Сервис сделок: создание, оплата (атомарно), модерация, споры, статусы."""
from __future__ import annotations

import logging
import secrets
from decimal import Decimal

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.base import get_commission_percent
from bot.db.models import Deal, Dispute, User
from bot.services import finance
from bot.services.finance import DuplicateOperation, InsufficientFunds, money

log = logging.getLogger("playerok.deals")


def gen_code() -> str:
    return f"PK-{secrets.token_hex(3).upper()}"


async def create_deal(session: AsyncSession, *, seller: User, category_id: int | None,
                      game_id: int | None, title: str, link: str | None,
                      price: Decimal, description: str | None = None) -> Deal:
    commission_pct = await get_commission_percent(session)
    price = money(price)
    commission = money(price * commission_pct / Decimal(100))
    seller_amount = money(price - commission)
    # уникальность кода
    code = gen_code()
    while (await session.execute(select(Deal.id).where(Deal.code == code))).scalar_one_or_none():
        code = gen_code()
    deal = Deal(code=code, seller_id=seller.id, category_id=category_id, game_id=game_id,
                title=title.strip()[:500], link=(link or "").strip()[:500] or None,
                description=(description or "").strip()[:1000] or None,
                price=price, commission=commission, seller_amount=seller_amount,
                status="WAITING_FOR_BUYER")
    seller.deals_created += 1
    session.add(deal)
    await session.commit()
    await session.refresh(deal)
    return deal


async def get_deal_by_code(session: AsyncSession, code: str) -> Deal | None:
    return (await session.execute(select(Deal).where(Deal.code == code.upper()))).scalar_one_or_none()


async def get_deal(session: AsyncSession, deal_id: int) -> Deal | None:
    return await session.get(Deal, deal_id)


async def my_deals(session: AsyncSession, user_id: int, tab: str):
    stmt = select(Deal).where(or_(Deal.seller_id == user_id, Deal.buyer_id == user_id))
    if tab == "active":
        stmt = stmt.where(Deal.status.in_([
            "CREATED", "WAITING_FOR_BUYER", "BUYER_OPENED", "WAITING_PAYMENT",
            "PAID", "FUNDS_FROZEN", "WAITING_MODERATION", "APPROVED", "DISPUTED"]))
    elif tab == "completed":
        stmt = stmt.where(Deal.status.in_(["COMPLETED", "REFUNDED"]))
    elif tab == "cancelled":
        stmt = stmt.where(Deal.status.in_(["CANCELLED", "REJECTED"]))
    elif tab == "disputes":
        stmt = stmt.where(Deal.status == "DISPUTED")
    stmt = stmt.order_by(Deal.updated_at.desc()).limit(50)
    return (await session.execute(stmt)).scalars().all()


async def moderation_queue(session: AsyncSession):
    stmt = select(Deal).where(Deal.status == "WAITING_MODERATION").order_by(Deal.updated_at.asc())
    return (await session.execute(stmt)).scalars().all()


async def mark_opened(session: AsyncSession, deal: Deal) -> None:
    if deal.status in ("WAITING_FOR_BUYER", "CREATED"):
        deal.status = "BUYER_OPENED"
        await session.commit()


async def pay_deal(session: AsyncSession, *, deal: Deal, buyer: User) -> Deal:
    """Атомарная оплата. Идемпотентность по key=pay:{deal.id} — защита от двойного нажатия."""
    if deal.seller_id == buyer.id:
        raise ValueError("SELLER_SELF")
    if deal.status not in ("BUYER_OPENED", "WAITING_FOR_BUYER", "CREATED", "WAITING_PAYMENT"):
        raise ValueError(f"BAD_STATUS:{deal.status}")
    try:
        # 1. списание с покупателя
        await finance.sub_available(
            session, buyer.id, deal.price, "PURCHASE", deal_id=deal.id,
            idem_key=f"PAY:{deal.id}:BUY", comment=f"Оплата сделки #{deal.code}")
        # 2. комиссия сервиса
        await finance.write_tx(
            session, user_id=buyer.id, amount=Decimal(0), type_="COMMISSION",
            deal_id=deal.id, idem_key=f"PAY:{deal.id}:COM",
            comment=f"Комиссия {money(deal.commission)} ₽ по сделке #{deal.code}")
        # 3. заморозка суммы продавца
        await finance.freeze(
            session, deal.seller_id, deal.seller_amount, deal_id=deal.id,
            idem_key=f"PAY:{deal.id}:FRZ", comment=f"Заморозка по сделке #{deal.code}")
        deal.buyer_id = buyer.id
        deal.status = "WAITING_MODERATION"
        await session.commit()
        return deal
    except DuplicateOperation:
        await session.rollback()
        # уже оплачено — вернём актуальное состояние
        fresh = await get_deal(session, deal.id)
        raise ValueError(f"ALREADY_PAID:{fresh.status if fresh else '?'}")
    except InsufficientFunds:
        await session.rollback()
        raise ValueError("NO_FUNDS")
    except Exception:
        await session.rollback()
        log.exception("pay_deal failed for deal %s", deal.id)
        raise


async def approve_deal(session: AsyncSession, deal: Deal) -> bool:
    """Одобрение модератором. Возвращает False, если сделка уже обработана (защита от повторных действий)."""
    if deal.status != "WAITING_MODERATION":
        return False
    try:
        await finance.unfreeze_to_available(
            session, deal.seller_id, deal.seller_amount, deal_id=deal.id,
            idem_key=f"MOD:{deal.id}:APP", comment=f"Разморозка после проверки #{deal.code}")
        deal.status = "COMPLETED"
        from bot.db.models import utcnow
        deal.completed_at = utcnow()
        for uid in (deal.seller_id, deal.buyer_id):
            u = await session.get(User, uid)
            if u:
                u.deals_completed += 1
        await session.commit()
        return True
    except Exception:
        await session.rollback()
        log.exception("approve_deal failed %s", deal.id)
        raise


async def reject_deal(session: AsyncSession, deal: Deal, note: str = "") -> bool:
    """Отклонение: покупателю возврат, сумма продавца снимается с frozen."""
    if deal.status != "WAITING_MODERATION":
        return False
    try:
        await finance.unfreeze_back(
            session, deal.seller_id, deal.seller_amount, deal_id=deal.id,
            idem_key=f"MOD:{deal.id}:REJ", comment=f"Отклонение сделки #{deal.code}")
        if deal.buyer_id:
            await finance.add_available(
                session, deal.buyer_id, deal.price, "REFUND", deal_id=deal.id,
                idem_key=f"MOD:{deal.id}:REF", comment=f"Возврат по сделке #{deal.code}")
        deal.status = "REJECTED" if not deal.buyer_id else "REFUNDED"
        deal.moderation_note = note[:500] if note else None
        await session.commit()
        return True
    except Exception:
        await session.rollback()
        log.exception("reject_deal failed %s", deal.id)
        raise


async def dispute_deal(session: AsyncSession, deal: Deal) -> bool:
    if deal.status not in ("WAITING_MODERATION", "PAID", "FUNDS_FROZEN", "BUYER_OPENED",
                           "WAITING_PAYMENT", "DISPUTED"):
        return False
    if deal.status == "DISPUTED":
        return True
    if deal.status in ("BUYER_OPENED", "WAITING_PAYMENT", "CREATED", "WAITING_FOR_BUYER"):
        return False  # спора нет, пока ничего не оплачено
    deal.status = "DISPUTED"
    await session.commit()
    return True


async def resolve_dispute(session: AsyncSession, dispute: Dispute, winner: str,
                          note: str = "") -> bool:
    """winner: 'seller' | 'buyer'. Защита от повторного решения по статусу."""
    deal = await get_deal(session, dispute.deal_id)
    if not deal or dispute.status != "OPEN":
        return False
    try:
        if deal.status == "DISPUTED" or deal.status in ("PAID", "FUNDS_FROZEN", "WAITING_MODERATION"):
            if winner == "seller":
                if deal.seller_amount > 0:
                    await finance.unfreeze_to_available(
                        session, deal.seller_id, deal.seller_amount, deal_id=deal.id,
                        idem_key=f"DSP:{dispute.id}:SEL", comment=f"Спор #{dispute.id} в пользу продавца")
                deal.status = "COMPLETED"
            else:
                if deal.seller_amount > 0:
                    await finance.unfreeze_back(
                        session, deal.seller_id, deal.seller_amount, deal_id=deal.id,
                        idem_key=f"DSP:{dispute.id}:UNF", comment=f"Спор #{dispute.id}: снятие заморозки")
                if deal.buyer_id:
                    await finance.add_available(
                        session, deal.buyer_id, deal.price, "REFUND", deal_id=deal.id,
                        idem_key=f"DSP:{dispute.id}:REF", comment=f"Возврат по спору #{dispute.id}")
                deal.status = "REFUNDED"
        dispute.status = "RESOLVED_SELLER" if winner == "seller" else "RESOLVED_BUYER"
        dispute.resolution = (note or f"Решение: {'продавец' if winner == 'seller' else 'покупатель'}")[:500]
        from bot.db.models import utcnow
        dispute.resolved_at = utcnow()
        await session.commit()
        return True
    except Exception:
        await session.rollback()
        log.exception("resolve_dispute failed %s", dispute.id)
        raise


async def cancel_deal(session: AsyncSession, deal: Deal, by_user_id: int) -> bool:
    """Отмена продавцом до оплаты."""
    if deal.seller_id != by_user_id:
        return False
    if deal.status not in ("CREATED", "WAITING_FOR_BUYER", "BUYER_OPENED", "WAITING_PAYMENT"):
        return False
    deal.status = "CANCELLED"
    await session.commit()
    return True


async def open_dispute_record(session: AsyncSession, *, deal: Deal, user: User,
                              reason: str, description: str) -> Dispute:
    d = Dispute(deal_id=deal.id, user_id=user.id, reason=reason[:128],
                description=description[:2000], status="OPEN")
    session.add(d)
    await session.commit()
    await session.refresh(d)
    return d


async def pending_disputes(session: AsyncSession):
    stmt = select(Dispute).where(Dispute.status == "OPEN").order_by(Dispute.created_at.asc())
    return (await session.execute(stmt)).scalars().all()
