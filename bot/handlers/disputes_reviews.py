"""Споры и отзывы."""
from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.base import get_setting
from bot.handlers.deal_create import esc
from bot.keyboard.kbs import (
    dispute_confirm_kb, dispute_reasons_kb, inl, review_skip_kb, stars_kb,
)
from bot.services import catalog, deals as dsvc, users as usvc
from bot.states.states import DisputeStates, ReviewStates
from bot.utils.renderer import render_screen, show
from bot.utils.texts import LINE, SUPPORT_USERNAME, err_text, fmt_money

log = logging.getLogger("playerok.disputes")
router = Router(name="disputes_reviews")


# ================= СПОРЫ =================

@router.callback_query(F.data.startswith("disp:start:"))
async def disp_start(call: CallbackQuery, state: FSMContext, session):
    deal_id = int(call.data.split(":")[2])
    deal = await dsvc.get_deal(session, deal_id)
    user = await usvc.get_user(session, call.from_user.id)
    if deal is None or user is None:
        await call.answer("Сделка не найдена", show_alert=True)
        return
    if user.id not in (deal.seller_id, deal.buyer_id):
        await call.answer("Только участники сделки могут открыть спор", show_alert=True)
        return
    if deal.status not in ("WAITING_MODERATION", "PAID", "FUNDS_FROZEN", "DISPUTED"):
        await call.answer("Спор доступен после оплаты сделки", show_alert=True)
        return
    await call.answer()
    await state.set_state(DisputeStates.reason)
    await state.update_data(disp={"deal_id": deal.id})
    await show(call, session,
               text=f"⚖️ <b>СОЗДАНИЕ СПОРА</b>\n\n{LINE}\n\n"
                    f"🆔 <b>Сделка: #{deal.code}</b>\n\n"
                    "<b>Выберите причину спора:</b>",
               kb=dispute_reasons_kb(deal.id), state=state)


@router.callback_query(DisputeStates.reason, F.data.startswith("disp:reason:"))
async def disp_reason(call: CallbackQuery, state: FSMContext, session):
    parts = call.data.split(":")
    deal_id = int(parts[2])
    idx = int(parts[3])
    from bot.keyboard.kbs import DISPUTE_REASONS
    reason = DISPUTE_REASONS[idx] if 0 <= idx < len(DISPUTE_REASONS) else "Другое"
    data = await state.get_data()
    disp = data.get("disp", {})
    disp["reason"] = reason
    await state.update_data(disp=disp)
    await state.set_state(DisputeStates.description)
    await call.answer()
    await show(call, session,
               text=f"⚖️ <b>СПОР: {esc(reason)}</b>\n\n{LINE}\n\n"
                    "<b>Подробно опишите ситуацию (что произошло, чего вы ожидаете):</b>\n\n"
                    "<b>Чем больше деталей — тем быстрее сотрудник примет решение. 🕵️</b>",
               kb=inl([[("❌ ОТМЕНА", f"deal:view:{deal_id}")], ("🏠 В МЕНЮ", "nav:menu")]),
               state=state)


@router.message(DisputeStates.description, F.text)
async def disp_desc(message: Message, state: FSMContext, session):
    desc = message.text.strip()
    if len(desc) < 10:
        await message.answer("⚠️ <b>Слишком короткое описание. Напишите минимум 10 символов.</b>",
                             parse_mode="HTML")
        return
    data = await state.get_data()
    disp = data.get("disp", {})
    deal = await dsvc.get_deal(session, disp.get("deal_id", 0))
    if deal is None:
        await state.clear()
        await message.answer(err_text(), parse_mode="HTML")
        return
    disp["description"] = desc[:2000]
    await state.update_data(disp=disp)
    await state.set_state(DisputeStates.confirm)
    await render_screen(message.bot, session, chat_id=message.chat.id,
                        text=_disp_confirm_text(deal, disp),
                        kb=dispute_confirm_kb(deal.id), state=state)


def _disp_confirm_text(deal, disp) -> str:
    return (f"⚖️ <b>ПРОВЕРЬТЕ СПОР</b>\n\n{LINE}\n\n"
            f"🆔 <b>Сделка: #{deal.code}</b>\n"
            f"💰 <b>Сумма: {fmt_money(deal.price)} ₽</b>\n"
            f"📌 <b>Причина: {esc(disp.get('reason', ''))}</b>\n\n"
            f"📝 <b>Описание:</b>\n<b>{esc(disp.get('description', '')[:800])}</b>\n\n"
            "<b>После отправки сделка будет приостановлена до решения ответственного "
            "сотрудника. ⏳</b>")


@router.callback_query(DisputeStates.confirm, F.data.startswith("disp:redesc:"))
async def disp_redesc(call: CallbackQuery, state: FSMContext, session):
    deal_id = int(call.data.split(":")[2])
    await state.set_state(DisputeStates.description)
    await call.answer()
    await show(call, session,
               text=f"✏️ <b>ИЗМЕНИТЕ ОПИСАНИЕ</b>\n\n{LINE}\n\n<b>Отправьте новый текст описания:</b>",
               kb=inl([[("❌ ОТМЕНА", f"deal:view:{deal_id}")], ("🏠 В МЕНЮ", "nav:menu")]),
               state=state)


@router.callback_query(DisputeStates.confirm, F.data.startswith("disp:submit:"))
async def disp_submit(call: CallbackQuery, state: FSMContext, session):
    deal_id = int(call.data.split(":")[2])
    data = await state.get_data()
    disp = data.get("disp", {})
    deal = await dsvc.get_deal(session, deal_id)
    user = await usvc.get_user(session, call.from_user.id)
    if deal is None or user is None or "description" not in disp:
        await call.answer("Данные устарели — попробуйте снова", show_alert=True)
        await state.clear()
        return
    if not await dsvc.dispute_deal(session, deal):
        await call.answer("Спор недоступен для текущего статуса", show_alert=True)
        await state.clear()
        await deal_card_refresh(call, session, state, deal_id)
        return
    record = await dsvc.open_dispute_record(
        session, deal=deal, user=user, reason=disp.get("reason", "Другое"),
        description=disp.get("description", ""))
    await state.clear()
    # уведомление админам
    from bot.config.settings import get_config
    admins = [get_config().OWNER_ID]
    st = await get_setting(session, "admin_ids", "")
    admins += [int(x) for x in st.replace(" ", "").split(",") if x.isdigit()]
    other = await usvc.get_user_by_id(
        session, deal.buyer_id if user.id == deal.seller_id else deal.seller_id)
    for a in dict.fromkeys(admins):
        try:
            await call.bot.send_message(
                a, f"⚖️ <b>НОВЫЙ СПОР #{record.id}</b>\n\n"
                  f"🆔 Сделка: <b>#{deal.code}</b>\n"
                  f"👤 От кого: <b>{user.nickname}</b>\n"
                  f"📌 Причина: <b>{esc(record.reason)}</b>\n\n"
                  f"<b>{esc(record.description[:600])}</b>",
                parse_mode="HTML")
        except Exception:
            log.warning("cannot notify admin %s about dispute", a)
    if other:
        try:
            await call.bot.send_message(
                other.tg_id,
                f"⚖️ <b>ПО СДЕЛКЕ #{deal.code} ОТКРЫТ СПОР</b>\n\n"
                "<b>Ответственный сотрудник изучит ситуацию. Сделка приостановлена.</b>",
                parse_mode="HTML")
        except Exception:
            pass
    await call.answer("✅ Спор отправлен")
    await show(call, session,
               text=f"⏳ <b>СПОР НА РАССМОТРЕНИИ</b>\n\n{LINE}\n\n"
                    f"🧾 <b>Номер спора: #{record.id}</b>\n"
                    f"🆔 <b>Сделка: #{deal.code}</b>\n\n"
                    "<b>Ответственный человек изучит обе стороны и примет решение.</b>\n\n"
                    "🔔 <b>Вы получите уведомление о результате.</b>",
               kb=inl([[("📋 МОИ СДЕЛКИ", "deals:menu")], ("🏠 В МЕНЮ", "nav:menu")]),
               state=state)


async def deal_card_refresh(call, session, state, deal_id):
    from bot.handlers.deals_list import deal_card
    await deal_card(call, session, state, deal_id)


# ================= ОТЗЫВЫ =================

@router.callback_query(F.data.startswith("rev:start:"))
async def rev_start(call: CallbackQuery, state: FSMContext, session):
    deal_id = int(call.data.split(":")[2])
    deal = await dsvc.get_deal(session, deal_id)
    user = await usvc.get_user(session, call.from_user.id)
    await call.answer()
    if deal is None or user is None:
        await show(call, session, text=err_text(),
                   kb=inl([("🏠 В МЕНЮ", "nav:menu")]), state=state)
        return
    if not await catalog.can_review(session, deal, user):
        await show(call, session,
                   text=f"⭐ <b>ОЦЕНИТЬ НЕЛЬЗЯ</b>\n\n{LINE}\n\n"
                        "<b>Отзыв можно оставить только один раз после успешного "
                        "завершения сделки участником которой вы являетесь.</b>",
                   kb=inl([[("📋 МОИ СДЕЛКИ", "deals:menu")], ("🏠 В МЕНЮ", "nav:menu")]),
                   state=state)
        return
    target = await usvc.get_user_by_id(
        session, deal.buyer_id if user.id == deal.seller_id else deal.seller_id)
    await state.set_state(ReviewStates.stars)
    await state.update_data(rev={"deal_id": deal.id})
    await show(call, session,
               text=f"⭐ <b>ОЦЕНКА СДЕЛКИ</b>\n\n{LINE}\n\n"
                    f"🆔 <b>Сделка: #{deal.code}</b>\n"
                    f"👤 <b>Кому: {target.nickname if target else '—'}</b>\n\n"
                    "<b>Поставьте от 1 до 5 звёзд 👇</b>",
               kb=stars_kb(deal.id), state=state)


@router.callback_query(ReviewStates.stars, F.data.startswith("rev:star:"))
async def rev_star(call: CallbackQuery, state: FSMContext, session):
    parts = call.data.split(":")
    deal_id, stars = int(parts[2]), int(parts[3])
    data = await state.get_data()
    rev = data.get("rev", {})
    rev["stars"] = stars
    await state.update_data(rev=rev)
    await state.set_state(ReviewStates.text)
    await call.answer()
    await show(call, session,
               text=f"⭐ <b>ВАША ОЦЕНКА: {'⭐' * stars}</b>\n\n{LINE}\n\n"
                    "<b>Теперь отправьте текст отзыва (или нажмите «без текста»):</b>",
               kb=review_skip_kb(deal_id), state=state)


@router.message(ReviewStates.text, F.text)
async def rev_text(message: Message, state: FSMContext, session):
    data = await state.get_data()
    rev = data.get("rev", {})
    deal_id = rev.get("deal_id")
    stars = rev.get("stars")
    if not deal_id or not stars:
        await state.clear()
        return
    rev["text"] = message.text.strip()[:500]
    await state.update_data(rev=rev)
    await send_review(message, state, session, deal_id, stars, rev["text"], is_message=True)


@router.callback_query(ReviewStates.text, F.data.startswith("rev:send:"))
async def rev_send_nostext(call: CallbackQuery, state: FSMContext, session):
    parts = call.data.split(":")
    deal_id, skip = int(parts[2]), parts[3]
    if skip != "skip":
        await call.answer()
        return
    data = await state.get_data()
    rev = data.get("rev", {})
    stars = rev.get("stars", 5)
    await call.answer()
    await send_review(call, state, session, deal_id, stars, "", is_message=False)


async def send_review(event, state: FSMContext, session, deal_id, stars, text,
                      is_message: bool):
    deal = await dsvc.get_deal(session, deal_id)
    user = await usvc.get_user(session, event.from_user.id)
    if deal is None or user is None:
        await state.clear()
        return
    ok = await catalog.add_review(session, deal=deal, author=user, stars=stars, text=text)
    await state.clear()
    if not ok:
        await show(event, session,
                   text=f"⚠️ <b>ОТЗЫВ НЕ СОХРАНЁН</b>\n\n{LINE}\n\n"
                        "<b>Возможно, вы уже оставляли отзыв по этой сделке.</b>",
                   kb=inl([[("📋 МОИ СДЕЛКИ", "deals:menu")], ("🏠 В МЕНЮ", "nav:menu")]),
                   state=state)
        return
    target_id = deal.buyer_id if user.id == deal.seller_id else deal.seller_id
    target = await usvc.get_user_by_id(session, target_id)
    if target:
        try:
            await event.bot.send_message(
                target.tg_id,
                f"⭐ <b>НОВЫЙ ОТЗЫВ О ВАС!</b>\n\n{LINE}\n\n"
                f"🆔 <b>Сделка: #{deal.code}</b>\n"
                f"<b>{'⭐' * stars}</b>\n\n"
                + (f"<i>{esc(text[:400])}</i>\n\n" if text else ""),
                parse_mode="HTML")
        except Exception:
            log.info("cannot notify review target %s", target.tg_id)
    await show(event, session,
               text=f"✅ <b>СПАСИБО ЗА ОТЗЫВ!</b>\n\n{LINE}\n\n"
                    f"<b>Ваша оценка {'⭐' * stars} сохранена.</b>\n\n"
                    "<b>Рейтинг помогает другим пользователям выбирать надёжных "
                    "партнёров. 🤝</b>",
               kb=inl([[("📋 МОИ СДЕЛКИ", "deals:menu")], ("🏠 В МЕНЮ", "nav:menu")]),
               state=state)
