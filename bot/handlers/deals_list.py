"""Мои сделки, карточка сделки, deep link, покупка, оплата."""
from __future__ import annotations

import logging

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config.settings import get_config
from bot.handlers.deal_create import esc
from bot.keyboard.kbs import (
    deal_buyer_active_kb, deal_buyer_kb, deal_done_kb, deal_pay_confirm_kb,
    deal_seller_kb, deals_list_kb, deals_menu_kb, inl,
)
from bot.services import deals as dsvc, users as usvc
from bot.utils.renderer import render_screen, show
from bot.utils.texts import (
    DEAL_CARD_TMPL, LINE, NO_DEALS, PAY_CONFIRM_TMPL, err_text, fmt_money,
    rating_text, status_line,
)

log = logging.getLogger("playerok.deals")
router = Router(name="deals_list")

TABS = {"active": "🟢 АКТИВНЫЕ", "completed": "✅ ЗАВЕРШЁННЫЕ",
        "cancelled": "❌ ОТМЕНЁННЫЕ", "disputes": "⚖️ СПОРЫ"}


def deal_link_for(bot: Bot, code: str) -> str:
    cfg = get_config()
    username = cfg.BOT_USERNAME.strip().lstrip("@") if cfg.BOT_USERNAME else None
    if not username:
        # синхронно получить нельзя — кэшируем при старте в workflow_data["bot_username"]
        username = getattr(bot, "_playerok_username", None) or "playerok_bot"
    return f"https://t.me/{username}?start=deal_{code}"


async def _ensure_user_registered(call, session) -> bool:
    user = await usvc.get_user(session, call.from_user.id)
    return bool(user and user.registered)


# ---------- Список сделок ----------

@router.callback_query(F.data == "deals:menu")
async def deals_menu_screen(call: CallbackQuery, state: FSMContext, session):
    ok = await _ensure_user_registered(call, session)
    await call.answer("" if ok else "Пройдите регистрацию: /start")
    if not ok:
        return
    await state.update_data(_last_section="deals")
    await show(call, session,
               text=f"📋 <b>МОИ СДЕЛКИ</b>\n\n{LINE}\n\n<b>Выберите раздел 👇</b>",
               kb=deals_menu_kb(), banner="DEALS", state=state)


@router.callback_query(F.data.startswith("deals:tab:"))
async def deals_tab(call: CallbackQuery, state: FSMContext, session):
    tab = call.data.split(":")[2]
    if tab not in TABS:
        tab = "active"
    user = await usvc.get_user(session, call.from_user.id)
    await call.answer()
    if user is None or not user.registered:
        await show(call, session, text="👤 <b>Сначала регистрация:</b> /start",
                   kb=inl([("🏠 В МЕНЮ", "nav:menu")]), state=state)
        return
    items = await dsvc.my_deals(session, user.id, tab)
    await state.update_data(_last_section="deals", _deals_tab=tab)
    if not items:
        empty_extra = "" if tab == "active" else "\n\n<b>В этом разделе пока пусто.</b>"
        text = NO_DEALS + empty_extra
        kb = inl([[("➕ СОЗДАТЬ СДЕЛКУ", "deal:new")], ("🔙 К РАЗДЕЛАМ", "deals:menu"),
                  ("🏠 В МЕНЮ", "nav:menu")])
        await show(call, session, text=text, kb=kb, banner="DEALS", state=state)
        return
    lines = [f"<b>{i+1}. #{d.code} — {esc(d.title[:40])}</b>\n   <b>{fmt_money(d.price)} ₽ · {status_line_plain(d.status)}</b>"
             for i, d in enumerate(items[:15])]
    text = (f"📋 <b>{TABS[tab]}</b>\n\n{LINE}\n\n" + "\n\n".join(lines))
    await show(call, session, text=text, kb=deals_list_kb(list(items), tab),
               banner="DEALS", state=state)


def status_line_plain(status: str) -> str:
    from bot.utils.texts import STATUS_EMOJIS, STATUS_LABELS
    return f"{STATUS_EMOJIS.get(status,'ℹ️')} {STATUS_LABELS.get(status, status)}"


# ---------- Карточка сделки ----------

async def deal_card(event, session: AsyncSession, state: FSMContext, deal_id: int,
                    *, notify_seller_opened: bool = False):
    deal = await dsvc.get_deal(session, deal_id)
    if deal is None:
        await show(event, session, text=err_text(), kb=inl([("🏠 В МЕНЮ", "nav:menu")]),
                   state=state)
        return
    viewer_tg_id = event.from_user.id
    viewer = await usvc.ensure_user(session, viewer_tg_id,
                                    getattr(event.from_user, "username", None),
                                    getattr(event.from_user, "first_name", None))
    seller = await usvc.get_user_by_id(session, deal.seller_id)
    category_name = "—"
    if deal.category:
        category_name = f"{deal.category.emoji} {deal.category.name}"
    if deal.game:
        category_name += f" · {deal.game.name}"
    is_seller = viewer.id == deal.seller_id
    is_buyer = deal.buyer_id == viewer.id

    text = DEAL_CARD_TMPL.format(
        code=deal.code, title=esc(deal.title[:120]), category=category_name,
        seller_nickname=seller.nickname if seller else "—",
        rating=rating_text(seller) if seller else "—",
        deals_count=seller.deals_created if seller else 0,
        price=fmt_money(deal.price),
        status_emoji=_status_emoji(deal.status), status_label=_status_label(deal.status))

    if deal.description:
        text += f"\n\n📝 <b>Описание:</b>\n<b>{esc(deal.description[:400])}</b>"

    kb = inl([("🏠 В МЕНЮ", "nav:menu")])
    if is_seller:
        kb = deal_seller_kb(deal.id, deal.status)
    elif is_buyer:
        if deal.status == "COMPLETED":
            kb = deal_done_kb(deal.id)
        elif deal.status in ("WAITING_MODERATION", "DISPUTED"):
            kb = deal_buyer_active_kb(deal.id, deal.status)
        else:
            kb = inl([[("🔙 НАЗАД", "deals:menu")], ("🏠 В МЕНЮ", "nav:menu")])
    else:
        # посторонний видит сделку (например по ссылке)
        if deal.status in ("WAITING_FOR_BUYER", "BUYER_OPENED", "CREATED", "WAITING_PAYMENT"):
            kb = deal_buyer_kb(deal.id)
            if viewer.id != deal.seller_id:
                await dsvc.mark_opened(session, deal)
                if notify_seller_opened and seller:
                    try:
                        await event.bot.send_message(
                            seller.tg_id,
                            f"👀 <b>Вашу сделку #{deal.code} открыл покупатель!</b>\n\n"
                            "<b>Отправьте ему ссылку и ждите оплаты. 🛡</b>",
                            parse_mode="HTML")
                    except Exception:
                        pass
        else:
            kb = inl([[("🆘 ПОДДЕРЖКА", "support:menu")], ("🏠 В МЕНЮ", "nav:menu")])

    await show(event, session, text=text, kb=kb, banner=None, state=state)


def _status_emoji(s):
    from bot.utils.texts import STATUS_EMOJIS
    return STATUS_EMOJIS.get(s, "ℹ️")


def _status_label(s):
    from bot.utils.texts import STATUS_LABELS
    return STATUS_LABELS.get(s, s)


@router.callback_query(F.data.startswith("deal:view:"))
async def deal_view(call: CallbackQuery, state: FSMContext, session):
    await call.answer()
    deal_id = int(call.data.split(":")[2])
    await deal_card(call, session, state, deal_id)


@router.message(F.text.startswith("/deal "))
async def deal_direct(message: Message, state: FSMContext, session):
    code = message.text.split(maxsplit=1)[1].strip()
    deal = await dsvc.get_deal_by_code(session, code)
    if deal is None:
        await message.answer(err_text("Сделка не найдена."), parse_mode="HTML",
                             reply_markup=inl([("🏠 В МЕНЮ", "nav:menu")]))
        return
    await deal_card(message, session, state, deal.id, notify_seller_opened=True)


# ---------- Deep link t.me/bot?start=deal_XXXX ----------

@router.message(CommandStart(deep_link=True))
async def start_with_deal(message: Message, command: CommandObject, state: FSMContext,
                          session, bot: Bot):
    """Обрабатывает /start deal_CODE — открывает карточку сделки.

    Регистрация и обычный /start обрабатываются ранее (см. run.py).
    Если deep-link не распознан — показываем главное меню (тупиков нет).
    """
    if not command or not command.args or not command.args.startswith("deal_"):
        from bot.handlers.common import menu_screen
        await menu_screen(message, session, state)
        return
    code = command.args.removeprefix("deal_").upper()
    deal = await dsvc.get_deal_by_code(session, code)
    if deal is None:
        await message.answer(err_text("Сделка не найдена или удалена."),
                             parse_mode="HTML",
                             reply_markup=inl([("🏠 В МЕНЮ", "nav:menu")]))
        return
    user = await usvc.get_user(session, message.from_user.id)
    if user is None or not user.registered:
        await message.answer(
            f"🤝 <b>СДЕЛКА #{deal.code}</b>\n\n"
            "<b>Чтобы открыть и оплатить эту сделку, сначала пройдите регистрацию.</b>",
            parse_mode="HTML")
        from bot.handlers.registration import cmd_start
        await cmd_start(message, state, session, bot)
        return
    await deal_card(message, session, state, deal.id, notify_seller_opened=True)


# ---------- Ссылка на сделку ----------

@router.callback_query(F.data.startswith("deal:link:"))
async def deal_share_link(call: CallbackQuery, session):
    deal_id = int(call.data.split(":")[2])
    deal = await dsvc.get_deal(session, deal_id)
    if deal is None or deal.seller_id != (await usvc.get_user(session, call.from_user.id)).id:
        await call.answer("Недоступно", show_alert=True)
        return
    link = deal_link_for(call.bot, deal.code)
    await call.answer()
    await call.message.copy_to(chat_id=call.from_user.id,
                               text=f"🔗 <b>ССЫЛКА НА СДЕЛКУ #{deal.code}:</b>\n\n<b>{link}</b>\n\n"
                                    "<b>Отправьте её покупателю 📤</b>",
                               parse_mode="HTML")


# ---------- Отмена сделки продавцом ----------

@router.callback_query(F.data.startswith("deal:cancel:"))
async def deal_cancel(call: CallbackQuery, session, state: FSMContext):
    deal_id = int(call.data.split(":")[2])
    deal = await dsvc.get_deal(session, deal_id)
    user = await usvc.get_user(session, call.from_user.id)
    if deal is None or user is None:
        await call.answer("Сделка не найдена", show_alert=True)
        return
    ok = await dsvc.cancel_deal(session, deal, user.id)
    await call.answer("✅ Сделка отменена" if ok else "Отмена недоступна для этого статуса",
                      show_alert=not ok)
    if ok:
        await deal_card(call, session, state, deal_id)
    else:
        await deal_card(call, session, state, deal_id)


# ---------- Оплата ----------

@router.callback_query(F.data.startswith("pay:start:"))
async def pay_start(call: CallbackQuery, state: FSMContext, session):
    deal_id = int(call.data.split(":")[2])
    deal = await dsvc.get_deal(session, deal_id)
    user = await usvc.get_user(session, call.from_user.id)
    if deal is None or user is None:
        await call.answer("Сделка не найдена", show_alert=True)
        return
    if deal.seller_id == user.id:
        await call.answer("Вы продавец этой сделки 🙂", show_alert=True)
        return
    if deal.status not in ("WAITING_FOR_BUYER", "BUYER_OPENED", "CREATED", "WAITING_PAYMENT"):
        await call.answer("Сделка уже оплачена или закрыта", show_alert=True)
        await deal_card(call, session, state, deal_id)
        return
    insufficient = ""
    if user.available_balance < deal.price:
        need = fmt_money(deal.price - user.available_balance)
        insufficient = (f"⚠️ <b>Недостаточно средств. Пополните баланс ещё на {need} ₽:</b>")
    text = PAY_CONFIRM_TMPL.format(
        title=esc(deal.title[:100]), price=fmt_money(deal.price),
        balance=fmt_money(user.available_balance),
        after=fmt_money(max(user.available_balance - deal.price, 0)),
        insufficient=insufficient)
    kb = deal_pay_confirm_kb(deal.id)
    if user.available_balance < deal.price:
        from bot.db.base import get_setting
        kb = inl([[("➕ ПОПОЛНИТЬ БАЛАНС", "dep:start")],
                  [("🔙 НАЗАД К СДЕЛКЕ", f"deal:view:{deal.id}")],
                  ("🏠 В МЕНЮ", "nav:menu")])
    await call.answer()
    await show(call, session, text=text, kb=kb, state=state)


@router.callback_query(F.data.startswith("pay:confirm:"))
async def pay_confirm(call: CallbackQuery, state: FSMContext, session):
    deal_id = int(call.data.split(":")[2])
    deal = await dsvc.get_deal(session, deal_id)
    user = await usvc.get_user(session, call.from_user.id)
    if deal is None or user is None:
        await call.answer("Сделка не найдена", show_alert=True)
        return
    try:
        paid = await dsvc.pay_deal(session, deal=deal, buyer=user)
    except ValueError as e:
        code = str(e)
        await session.rollback()
        deal = await dsvc.get_deal(session, deal_id)
        if code == "NO_FUNDS":
            await call.answer("Недостаточно средств", show_alert=True)
            await show(call, session,
                       text=f"💸 <b>НЕДОСТАТОЧНО СРЕДСТВ</b>\n\n{LINE}\n\n"
                            "<b>Пополните баланс и попробуйте снова.</b>",
                       kb=inl([[("➕ ПОПОЛНИТЬ", "dep:start")],
                               [("🔙 НАЗАД", f"deal:view:{deal_id}")],
                               ("🏠 В МЕНЮ", "nav:menu")]), state=state)
        elif code.startswith("ALREADY_PAID"):
            await call.answer("Сделка уже оплачена", show_alert=True)
            await deal_card(call, session, state, deal_id)
        elif code == "SELLER_SELF":
            await call.answer("Вы продавец этой сделки", show_alert=True)
        else:
            await call.answer("Сделка недоступна", show_alert=True)
            if deal:
                await deal_card(call, session, state, deal_id)
        return
    except Exception:
        log.exception("payment error")
        await session.rollback()
        await call.answer("Произошла ошибка", show_alert=True)
        await show(call, session, text=err_text(),
                   kb=inl([[("🔙 НАЗАД", f"deal:view:{deal_id}")],
                           ("🏠 В МЕНЮ", "nav:menu")]), state=state)
        return

    seller = await usvc.get_user_by_id(session, paid.seller_id)
    # уведомления обеим сторонам
    buyer_notif = (f"✅ <b>ОПЛАТА ПРОШЛА</b>\n\n{LINE}\n\n"
                   f"🆔 <b>Сделка: #{paid.code}</b>\n"
                   f"💰 <b>Списано: {fmt_money(paid.price)} ₽</b>\n"
                   f"🔒 <b>Средства защищены до проверки сделки.</b>\n\n"
                   "<b>Как только модератор проверит сделку — вы получите уведомление. 🛡</b>")
    seller_notif = (f"💰 <b>ВАШУ СДЕЛКУ ОПЛАТИЛИ!</b>\n\n{LINE}\n\n"
                    f"🆔 <b>Сделка: #{paid.code}</b>\n"
                    f"💰 <b>Цена: {fmt_money(paid.price)} ₽</b>\n"
                    f"💸 <b>Комиссия сервиса: {fmt_money(paid.commission)} ₽</b>\n"
                    f"🔒 <b>Заморожено до проверки: {fmt_money(paid.seller_amount)} ₽</b>\n\n"
                    "<b>Передайте товар покупателю. Сделка отправлена на проверку ⏳</b>")
    try:
        await call.bot.send_message(user.tg_id, buyer_notif, parse_mode="HTML")
    except Exception:
        log.warning("cannot notify buyer %s", user.tg_id)
    if seller:
        try:
            await call.bot.send_message(seller.tg_id, seller_notif, parse_mode="HTML")
        except Exception:
            log.warning("cannot notify seller %s", seller.tg_id)
    await call.answer("✅ Оплачено!")
    # переводим в статус ожидания проверки
    data = await state.get_data()
    await show(call, session,
               text=f"⏳ <b>ОЖИДАЕТ ПРОВЕРКИ</b>\n\n{LINE}\n\n"
                    f"🆔 <b>Сделка: #{paid.code}</b>\n\n"
                    "<b>Оплата прошла успешно. Ответственный сотрудник проверяет сделку.</b>\n\n"
                    "<b>Вы получите уведомление о результате. 🛡</b>",
               kb=inl([[("📋 МОИ СДЕЛКИ", "deals:menu")], ("🏠 В МЕНЮ", "nav:menu")]),
               state=state)
