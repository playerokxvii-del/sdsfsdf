"""Сервис платежей (пополнение) и выводов — обрабатываются реальным человеком."""
from __future__ import annotations

import logging
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.base import get_setting
from bot.db.models import Payment, User, Withdrawal, utcnow
from bot.services import finance
from bot.services.finance import DuplicateOperation, InsufficientFunds, money

log = logging.getLogger("playerok.pay")


async def create_deposit_request(session: AsyncSession, user: User, amount: Decimal) -> Payment:
    p = Payment(user_id=user.id, amount=money(amount), method="manual", status="PENDING")
    session.add(p)
    await session.commit()
    await session.refresh(p)
    return p


async def pending_payments(session: AsyncSession):
    stmt = select(Payment).where(Payment.status == "PENDING").order_by(Payment.created_at.asc())
    return (await session.execute(stmt)).scalars().all()


async def recent_payments(session: AsyncSession, limit: int = 20):
    stmt = select(Payment).order_by(Payment.created_at.desc()).limit(limit)
    return (await session.execute(stmt)).scalars().all()


async def approve_payment(session: AsyncSession, payment: Payment) -> bool:
    """Защита от повторных действий: проверяем статус + idempotency-ключ."""
    if payment.status != "PENDING":
        return False
    try:
        await finance.add_available(
            session, payment.user_id, payment.amount, "DEPOSIT",
            idem_key=f"PAYMENT:{payment.id}:APPR",
            comment=f"Пополнение баланса #{payment.id}")
        payment.status = "COMPLETED"
        payment.processed_at = utcnow()
        await session.commit()
        return True
    except DuplicateOperation:
        await session.rollback()
        return False
    except Exception:
        await session.rollback()
        log.exception("approve_payment %s failed", payment.id)
        raise


async def reject_payment(session: AsyncSession, payment: Payment, note: str = "") -> bool:
    if payment.status != "PENDING":
        return False
    payment.status = "REJECTED"
    payment.comment = (note or "Отклонено")[:500]
    payment.processed_at = utcnow()
    await session.commit()
    return True


async def create_withdrawal(session: AsyncSession, user: User, amount: Decimal,
                            details: str) -> Withdrawal | str:
    """Возвращает Withdrawal либо код ошибки: NO_FUNDS / BELOW_MIN."""
    amount = money(amount)
    min_w = Decimal(await get_setting(session, "min_withdraw", "100"))
    if amount < min_w:
        return "BELOW_MIN"
    if user.available_balance < amount:
        return "NO_FUNDS"
    w = Withdrawal(user_id=user.id, amount=amount, details=details.strip()[:500],
                   status="PENDING")
    session.add(w)
    await session.flush()
    try:
        # при создании заявки сумма списывается с available и учитывается как WITHDRAWAL (PENDING)
        await finance.sub_available(
            session, user.id, amount, "WITHDRAWAL",
            idem_key=f"WDR:{w.id}:HOLD", comment=f"Заявка на вывод #{w.id}")
        w.status = "PENDING"
        await session.commit()
        return w
    except InsufficientFunds:
        await session.rollback()
        return "NO_FUNDS"


async def pending_withdrawals(session: AsyncSession):
    stmt = select(Withdrawal).where(Withdrawal.status.in_(["PENDING", "APPROVED"])) \
        .order_by(Withdrawal.created_at.asc())
    return (await session.execute(stmt)).scalars().all()


async def recent_withdrawals(session: AsyncSession, limit: int = 20):
    stmt = select(Withdrawal).order_by(Withdrawal.created_at.desc()).limit(limit)
    return (await session.execute(stmt)).scalars().all()


async def complete_withdrawal(session: AsyncSession, w: Withdrawal) -> bool:
    """Средства уже списаны при подаче заявки — здесь только финальный статус."""
    if w.status not in ("PENDING", "APPROVED"):
        return False
    w.status = "COMPLETED"
    w.processed_at = utcnow()
    await session.commit()
    return True


async def reject_withdrawal(session: AsyncSession, w: Withdrawal, note: str = "") -> bool:
    """Отклонение — возвращаем сумму на available."""
    if w.status not in ("PENDING", "APPROVED"):
        return False
    try:
        await finance.add_available(
            session, w.user_id, w.amount, "REFUND",
            idem_key=f"WDR:{w.id}:BACK", comment=f"Возврат по отклонённому выводу #{w.id}")
        w.status = "REJECTED"
        w.processed_at = utcnow()
        await session.commit()
        return True
    except DuplicateOperation:
        await session.rollback()
        return False
    except Exception:
        await session.rollback()
        log.exception("reject_withdrawal %s failed", w.id)
        raise
