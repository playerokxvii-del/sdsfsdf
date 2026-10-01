"""Кошелёк: пополнение, вывод, история операций."""
from __future__ import annotations

import hashlib
import logging
import re
from decimal import Decimal, InvalidOperation

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.base import get_setting
from bot.db.models import Transaction
from bot.handlers.deal_create import esc
from bot.keyboard.kbs import (
    deposit_amount_kb, deposit_confirm_kb, history_kb, inl, wallet_kb,
    withdraw_confirm_kb, HISTORY_PAGE_SIZE,
)
from bot.services import payments as psvc, users as usvc
from bot.states.states import WithdrawStates, DepositStates
from bot.utils.renderer import render_screen, show
from bot.utils.texts import (
    LINE, WALLET_HOW, WALLET_TEXT, err_text, fmt_money,
)

log = logging.getLogger("playerok.wallet")
router = Router(name="wallet")


def parse_amount(text: str) -> Decimal | None:
    t = text.replace(",", ".").replace("₽", "").replace("руб", "").strip()
    if not re.fullmatch(r"\d{1,9}(\.\d{1,2})?", t):
        return None
    try:
        v = Decimal(t)
    except InvalidOperation:
        return None
    if v <= 0 or v > Decimal("10000000"):
        return None
    return v


# ---------- Экран кошелька ----------

async def wallet_screen(event, session: AsyncSession, state: FSMContext):
    user = await usvc.get_user(session, event.from_user.id)
    if isinstance(event, CallbackQuery):
        await event.answer()
    if user is None or not user.registered:
        await show(event, session, text="👤 <b>Сначала регистрация:</b> /start",
                   kb=inl([("🏠 В МЕНЮ", "nav:menu")]), state=state)
        return
    total = user.available_balance + user.frozen_balance
    text = WALLET_TEXT.format(balance=fmt_money(user.available_balance),
                              frozen=fmt_money(user.frozen_balance),
                              total=fmt_money(total))
    await state.update_data(_last_section="wallet")
    await show(event, session, text=text, kb=wallet_kb(), banner="WALLET", state=state)


@router.callback_query(F.data == "wallet:menu")
async def wallet_menu(call: CallbackQuery, state: FSMContext, session):
    await wallet_screen(call, session, state)


@router.callback_query(F.data == "wallet:how")
async def wallet_how(call: CallbackQuery, state: FSMContext, session):
    await call.answer()
    await show(call, session, text=WALLET_HOW,
               kb=inl([[("🔙 КОШЕЛЁК", "wallet:menu")], ("🏠 В МЕНЮ", "nav:menu")]),
               banner="WALLET", state=state)


# ---------- Пополнение ----------

@router.callback_query(F.data == "dep:start")
async def dep_start(call: CallbackQuery, state: FSMContext, session):
    reqs = await get_setting(session, "payment_requisites", "Реквизиты не заданы.")
    min_dep = await get_setting(session, "min_deposit", "50")
    await call.answer()
    await state.set_state(DepositStates.amount)
    await state.update_data(_last_section="wallet")
    text = (f"➕ <b>ПОПОЛНЕНИЕ БАЛАНСА</b>\n\n{LINE}\n\n"
            f"<b>{esc(reqs)}</b>\n\n"
            f"💵 <b>Минимальная сумма: {min_dep} ₽.</b>\n\n"
            "<b>Выберите сумму или введите свою 👇</b>")
    await show(call, session, text=text, kb=deposit_amount_kb(), banner="WALLET", state=state)


@router.callback_query(F.data.startswith("dep:quick:"))
async def dep_quick(call: CallbackQuery, state: FSMContext, session):
    amount = call.data.split(":")[2]
    await dep_show_confirm(call, state, session, amount)


@router.callback_query(F.data == "dep:custom")
async def dep_custom(call: CallbackQuery, state: FSMContext, session):
    await call.answer()
    await state.set_state(DepositStates.amount)
    await show(call, session,
               text=f"✏️ <b>ВВЕДИТЕ СУММУ ПОПОЛНЕНИЯ</b>\n\n{LINE}\n\n"
                    "<b>Отправьте число в рублях, например: 750</b>",
               kb=inl([[("❌ ОТМЕНА", "wallet:menu")], ("🏠 В МЕНЮ", "nav:menu")]),
               banner="WALLET", state=state)


@router.message(DepositStates.amount, F.text)
async def dep_amount_message(message: Message, state: FSMContext, session):
    amount = parse_amount(message.text)
    if amount is None:
        await message.answer("❌ <b>Введите корректную сумму числом.</b>", parse_mode="HTML")
        return
    min_dep = Decimal(await get_setting(session, "min_deposit", "50"))
    if amount < min_dep:
        await message.answer(f"⚠️ <b>Минимальная сумма пополнения — {fmt_money(min_dep)} ₽.</b>",
                             parse_mode="HTML")
        return
    await state.set_state(DepositStates.confirm)
    await render_screen(message.bot, session, chat_id=message.chat.id,
                        text=_dep_confirm_text(amount), kb=deposit_confirm_kb(str(amount)),
                        banner_section="WALLET", state=state)


def _dep_confirm_text(amount: Decimal) -> str:
    return (f"💳 <b>ЗАЯВКА НА ПОПОЛНЕНИЕ</b>\n\n{LINE}\n\n"
            f"💰 <b>Сумма: {fmt_money(amount)} ₽</b>\n\n"
            "<b>После отправки заявки платёж проверит ответственный сотрудник.</b>\n\n"
            "⏳ <b>Как только перевод будет подтверждён — вы получите уведомление, "
            "и средства появятся на балансе. ✅</b>")


async def dep_show_confirm(call: CallbackQuery, state: FSMContext, session, amount_str: str):
    amount = parse_amount(amount_str)
    if amount is None:
        await call.answer("Некорректная сумма", show_alert=True)
        return
    await call.answer()
    await state.set_state(DepositStates.confirm)
    await show(call, session, text=_dep_confirm_text(amount),
               kb=deposit_confirm_kb(str(amount)), banner="WALLET", state=state)


@router.callback_query(F.data.startswith("dep:submit:"))
async def dep_submit(call: CallbackQuery, state: FSMContext, session):
    amount = parse_amount(call.data.split(":")[2])
    user = await usvc.get_user(session, call.from_user.id)
    if amount is None or user is None:
        await call.answer("Данные устарели — попробуйте снова", show_alert=True)
        await wallet_screen(call, session, state)
        return
    p = await psvc.create_deposit_request(session, user, amount)
    await state.clear()
    # уведомление админам
    from bot.config.settings import get_config
    admins = [get_config().OWNER_ID]
    st = await get_setting(session, "admin_ids", "")
    admins += [int(x) for x in st.replace(" ", "").split(",") if x.isdigit()]
    for a in dict.fromkeys(admins):
        try:
            await call.bot.send_message(
                a, f"💳 <b>НОВАЯ ЗАЯВКА НА ПОПОЛНЕНИЕ</b>\n\n"
                  f"🆔 Платёж: #{p.id}\n"
                  f"👤 Пользователь: <b>{user.nickname}</b> (tg {user.tg_id})\n"
                  f"💰 Сумма: <b>{fmt_money(p.amount)} ₽</b>\n\n"
                  "<b>Проверьте перевод и подтвердите заявку в разделе «💳 ПЛАТЕЖИ».</b>",
                parse_mode="HTML")
        except Exception:
            log.warning("cannot notify admin %s about payment", a)
    await call.answer("✅ Заявка создана")
    await show(call, session,
               text=f"⏳ <b>ЗАЯВКА СОЗДАНА!</b>\n\n{LINE}\n\n"
                    f"💰 <b>Сумма: {fmt_money(amount)} ₽</b>\n"
                    f"🧾 <b>Номер заявки: #{p.id}</b>\n\n"
                    "<b>Ответственный сотрудник проверяет платежи вручную.</b>\n\n"
                    "🔔 <b>Вы получите уведомление, как только баланс будет пополнен.</b>",
               kb=inl([[("💰 КОШЕЛЁК", "wallet:menu")], ("🏠 В МЕНЮ", "nav:menu")]),
               banner="WALLET", state=state)


# ---------- Вывод ----------

@router.callback_query(F.data == "wd:start")
async def wd_start(call: CallbackQuery, state: FSMContext, session):
    user = await usvc.get_user(session, call.from_user.id)
    min_w = await get_setting(session, "min_withdraw", "100")
    hint = await get_setting(session, "withdraw_details_hint", "Отправьте реквизиты сообщением.")
    if user is None:
        await call.answer()
        await wallet_screen(call, session, state)
        return
    await call.answer()
    await state.set_state(WithdrawStates.amount)
    await state.update_data(_last_section="wallet")
    await show(call, session,
               text=f"💸 <b>ВЫВОД СРЕДСТВ</b>\n\n{LINE}\n\n"
                    f"💵 <b>Доступно: {fmt_money(user.available_balance)} ₽</b>\n"
                    f"📉 <b>Минимальная сумма вывода: {min_w} ₽</b>\n\n"
                    "<b>1️⃣ Отправьте сумму для вывода числом:</b>",
               kb=inl([[("❌ ОТМЕНА", "wallet:menu")], ("🏠 В МЕНЮ", "nav:menu")]),
               banner="WALLET", state=state)


@router.message(WithdrawStates.amount, F.text)
async def wd_amount(message: Message, state: FSMContext, session):
    amount = parse_amount(message.text)
    if amount is None:
        await message.answer("❌ <b>Введите сумму числом.</b>", parse_mode="HTML")
        return
    user = await usvc.get_user(session, message.from_user.id)
    min_w = Decimal(await get_setting(session, "min_withdraw", "100"))
    if amount < min_w:
        await message.answer(f"⚠️ <b>Минимум для вывода — {fmt_money(min_w)} ₽.</b>",
                             parse_mode="HTML")
        return
    if user is None or user.available_balance < amount:
        await message.answer(
            f"⚠️ <b>Недостаточно доступных средств.</b>\n"
            f"<b>Доступно: {fmt_money(user.available_balance) if user else 0} ₽</b>",
            parse_mode="HTML")
        return
    await state.set_state(WithdrawStates.details)
    await state.update_data(wd={"amount": str(amount)})
    hint = await get_setting(session, "withdraw_details_hint", "Отправьте реквизиты.")
    await render_screen(message.bot, session, chat_id=message.chat.id,
                        text=f"💸 <b>ВЫВОД {fmt_money(amount)} ₽</b>\n\n{LINE}\n\n"
                             "<b>2️⃣ Отправьте реквизиты для перевода:</b>\n\n"
                             f"<b>{esc(hint)}</b>",
                        kb=inl([[("❌ ОТМЕНА", "wallet:menu")], ("🏠 В МЕНЮ", "nav:menu")]),
                        banner_section="WALLET", state=state)


@router.message(WithdrawStates.details, F.text)
async def wd_details(message: Message, state: FSMContext, session):
    details = message.text.strip()
    if len(details) < 8:
        await message.answer("❌ <b>Реквизиты слишком короткие. Проверьте и отправьте заново.</b>",
                             parse_mode="HTML")
        return
    data = await state.get_data()
    wd = data.get("wd", {})
    amount = parse_amount(wd.get("amount", ""))
    if amount is None:
        await state.clear()
        await wallet_screen(message, session, state)
        return
    dhash = hashlib.sha256(details.encode()).hexdigest()[:12]
    await state.set_state(WithdrawStates.confirm)
    await render_screen(message.bot, session, chat_id=message.chat.id,
                        text=f"💸 <b>ПОДТВЕРДИТЕ ВЫВОД</b>\n\n{LINE}\n\n"
                             f"💰 <b>Сумма: {fmt_money(amount)} ₽</b>\n"
                             f"🧾 <b>Реквизиты:</b>\n<b>{esc(details[:400])}</b>\n\n"
                             "<b>Заявку обработает ответственный сотрудник. ⏳</b>",
                        kb=withdraw_confirm_kb(str(amount), dhash),
                        banner_section="WALLET", state=state)
    wd["details"] = details
    await state.update_data(wd=wd)


@router.callback_query(F.data == "wd:restart")
async def wd_restart(call: CallbackQuery, state: FSMContext, session):
    await wd_start(call, state, session)


@router.callback_query(F.data.startswith("wd:submit:"))
async def wd_submit(call: CallbackQuery, state: FSMContext, session):
    parts = call.data.split(":")
    amount = parse_amount(parts[2])
    dhash = parts[3]
    data = await state.get_data()
    wd = data.get("wd", {})
    details = wd.get("details", "")
    if amount is None or not details or hashlib.sha256(details.encode()).hexdigest()[:12] != dhash:
        await call.answer("Данные устарели — начните заново", show_alert=True)
        await wallet_screen(call, session, state)
        return
    user = await usvc.get_user(session, call.from_user.id)
    if user is None:
        await call.answer()
        return
    result = await psvc.create_withdrawal(session, user, amount, details)
    if isinstance(result, str):
        await call.answer({"NO_FUNDS": "Недостаточно средств",
                           "BELOW_MIN": "Сумма ниже минимума"}.get(result, "Ошибка"),
                          show_alert=True)
        await wallet_screen(call, session, state)
        return
    w = result
    await state.clear()
    from bot.config.settings import get_config
    admins = [get_config().OWNER_ID]
    st = await get_setting(session, "admin_ids", "")
    admins += [int(x) for x in st.replace(" ", "").split(",") if x.isdigit()]
    for a in dict.fromkeys(admins):
        try:
            await call.bot.send_message(
                a, f"💸 <b>НОВАЯ ЗАЯВКА НА ВЫВОД</b>\n\n"
                  f"🆔 Заявка: #{w.id}\n"
                  f"👤 <b>{user.nickname}</b> (tg {user.tg_id})\n"
                  f"💰 Сумма: <b>{fmt_money(w.amount)} ₽</b>\n"
                  f"🧾 Реквизиты:\n<b>{esc(w.details[:400])}</b>",
                parse_mode="HTML")
        except Exception:
            log.warning("cannot notify admin %s about withdrawal", a)
    await call.answer("✅ Заявка создана")
    await show(call, session,
               text=f"⏳ <b>ЗАЯВКА НА ВЫВОД СОЗДАНА</b>\n\n{LINE}\n\n"
                    f"🧾 <b>Номер: #{w.id}</b>\n"
                    f"💰 <b>Сумма: {fmt_money(w.amount)} ₽</b>\n\n"
                    "<b>Статус: На обработке. Ответственный сотрудник выполнит перевод "
                    "и подтвердит заявку.</b>\n\n"
                    "🔔 <b>Мы уведомим вас о результате.</b>",
               kb=inl([[("💰 КОШЕЛЁК", "wallet:menu")], ("🏠 В МЕНЮ", "nav:menu")]),
               banner="WALLET", state=state)


# ---------- История операций ----------

TYPE_LABELS = {
    "DEPOSIT": "➕ Пополнение",
    "PURCHASE": "🛒 Покупка",
    "COMMISSION": "💸 Комиссия",
    "FREEZE": "🔒 Заморозка",
    "UNFREEZE": "🔓 Разморозка",
    "WITHDRAWAL": "💸 Вывод",
    "REFUND": "🔄 Возврат",
    "ADJUSTMENT": "⚖️ Корректировка",
}


@router.callback_query(F.data.startswith("hist:"))
async def history(call: CallbackQuery, state: FSMContext, session):
    page = int(call.data.split(":")[1]) if ":" in call.data and call.data.split(":")[1].isdigit() else 0
    user = await usvc.get_user(session, call.from_user.id)
    await call.answer()
    if user is None:
        await wallet_screen(call, session, state)
        return
    from sqlalchemy import func
    total = (await session.execute(
        select(func.count(Transaction.id)).where(Transaction.user_id == user.id)
    )).scalar_one()
    stmt = (select(Transaction).where(Transaction.user_id == user.id)
            .order_by(Transaction.created_at.desc())
            .limit(HISTORY_PAGE_SIZE).offset(page * HISTORY_PAGE_SIZE))
    txs = (await session.execute(stmt)).scalars().all()
    if not txs:
        text = f"📜 <b>ИСТОРИЯ ОПЕРАЦИЙ ПУСТА</b>\n\n{LINE}\n\n<b>Здесь появятся все движения средств: пополнения, покупки, заморозки и выводы. 🧾</b>"
        await show(call, session, text=text,
                   kb=inl([[("🔙 КОШЕЛЁК", "wallet:menu")], ("🏠 В МЕНЮ", "nav:menu")]),
                   banner="WALLET", state=state)
        return
    lines = []
    for t in txs:
        sign = "+" if t.amount > 0 else ("−" if t.amount < 0 else "±")
        amt = f"{sign}{fmt_money(abs(t.amount))} ₽"
        label = TYPE_LABELS.get(t.type, t.type)
        dt_ = t.created_at.strftime("%d.%m.%Y %H:%M")
        comment = f"\n   <b>{esc((t.comment or '')[:80])}</b>" if t.comment else ""
        lines.append(f"<b>{label} · {amt}</b>\n   <b>{dt_} · {t.status}</b>{comment}")
    pages_total = (total + HISTORY_PAGE_SIZE - 1) // HISTORY_PAGE_SIZE
    text = (f"📜 <b>ИСТОРИЯ ОПЕРАЦИЙ</b>\n\n{LINE}\n\n"
            + "\n\n".join(lines)
            + f"\n\n📄 <b>Страница {page + 1} из {pages_total}</b>")
    kb = history_kb(page, has_next=(page + 1 < pages_total), has_prev=page > 0)
    await show(call, session, text=text, kb=kb, banner="WALLET", state=state)
