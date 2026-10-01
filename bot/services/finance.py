"""Финансовый сервис: атомарные операции с балансами + ledger с idempotency."""
from __future__ import annotations

import logging
import uuid
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import Transaction, User

log = logging.getLogger("playerok.finance")

Q2 = Decimal("0.01")


class InsufficientFunds(Exception):
    pass


class DuplicateOperation(Exception):
    """Идемпотентность: операция уже выполнена."""
    def __init__(self, tx: Transaction):
        self.tx = tx
        super().__init__(f"Duplicate: {tx.transaction_uid}")


def money(v) -> Decimal:
    return Decimal(str(v)).quantize(Q2, rounding=ROUND_HALF_UP)


async def _get_user_for_update(session: AsyncSession, user_id: int) -> User:
    """SELECT ... FOR UPDATE на PostgreSQL; SQLite сериализует транзакции сам."""
    stmt = select(User).where(User.id == user_id)
    bind = session.get_bind()
    if bind.dialect.name == "postgresql":
        stmt = stmt.with_for_update()
    user = (await session.execute(stmt)).scalar_one()
    return user


async def write_tx(session: AsyncSession, *, user_id: int, amount: Decimal, type_: str,
                   deal_id: int | None = None, idem_key: str | None = None,
                   comment: str | None = None, status: str = "COMPLETED") -> Transaction:
    if idem_key:
        existing = (await session.execute(
            select(Transaction).where(Transaction.idempotency_key == idem_key)
        )).scalar_one_or_none()
        if existing is not None:
            raise DuplicateOperation(existing)
    tx = Transaction(
        transaction_uid=f"TX-{uuid.uuid4().hex[:12].upper()}",
        user_id=user_id, amount=money(amount), type=type_, status=status,
        related_deal_id=deal_id, idempotency_key=idem_key, comment=comment,
    )
    if status == "COMPLETED":
        from bot.db.models import utcnow
        tx.completed_at = utcnow()
    session.add(tx)
    return tx


async def add_available(session: AsyncSession, user_id: int, amount: Decimal,
                        type_: str, *, deal_id=None, idem_key=None, comment=None) -> Transaction:
    user = await _get_user_for_update(session, user_id)
    user.available_balance = money(user.available_balance + amount)
    return await write_tx(session, user_id=user_id, amount=amount, type_=type_,
                          deal_id=deal_id, idem_key=idem_key, comment=comment)


async def sub_available(session: AsyncSession, user_id: int, amount: Decimal,
                        type_: str, *, deal_id=None, idem_key=None, comment=None) -> Transaction:
    user = await _get_user_for_update(session, user_id)
    amount = money(amount)
    if user.available_balance < amount:
        raise InsufficientFunds()
    user.available_balance = money(user.available_balance - amount)
    return await write_tx(session, user_id=user_id, amount=-amount, type_=type_,
                          deal_id=deal_id, idem_key=idem_key, comment=comment)


async def freeze(session: AsyncSession, user_id: int, amount: Decimal,
                 *, deal_id=None, idem_key=None, comment=None) -> Transaction:
    user = await _get_user_for_update(session, user_id)
    user.frozen_balance = money(user.frozen_balance + amount)
    return await write_tx(session, user_id=user_id, amount=amount, type_="FREEZE",
                          deal_id=deal_id, idem_key=idem_key, comment=comment)


async def unfreeze_to_available(session: AsyncSession, user_id: int, amount: Decimal,
                                *, deal_id=None, idem_key=None, comment=None) -> Transaction:
    """Снять с frozen и зачислить в available (одобрение модерации)."""
    user = await _get_user_for_update(session, user_id)
    amount = money(amount)
    if user.frozen_balance < amount:
        raise InsufficientFunds()
    user.frozen_balance = money(user.frozen_balance - amount)
    user.available_balance = money(user.available_balance + amount)
    return await write_tx(session, user_id=user_id, amount=amount, type_="UNFREEZE",
                          deal_id=deal_id, idem_key=idem_key, comment=comment)


async def unfreeze_back(session: AsyncSession, user_id: int, amount: Decimal,
                        *, deal_id=None, idem_key=None, comment=None) -> Transaction:
    """Снять с frozen без зачисления (отклонение/спор не в пользу продавца)."""
    user = await _get_user_for_update(session, user_id)
    amount = money(amount)
    if user.frozen_balance < amount:
        raise InsufficientFunds()
    user.frozen_balance = money(user.frozen_balance - amount)
    return await write_tx(session, user_id=user_id, amount=-amount, type_="UNFREEZE",
                          deal_id=deal_id, idem_key=idem_key, comment=comment)
