"""Админ: платежи, выводы, споры, отзывы, тикеты, логи."""
from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy import func, select

from bot.admin.main import guard, log
from bot.db.base import get_setting
from bot.db.models import Dispute, Payment, Review, SupportTicket, User, Withdrawal
from bot.handlers.deal_create import esc
from bot.keyboard.kbs import inl
from bot.services import catalog, deals as dsvc, payments as psvc, users as usvc
from bot.states.states import AdminStates
from bot.utils.renderer import show
from bot.utils.texts import LINE, fmt_money, status_line_plain

router = Router(name="admin_finance")


async def notify_user(bot, tg_id: int, text: str):
    try:
        await bot.send_message(tg_id, text, parse_mode="HTML")
    except Exception:
        log.info("cannot notify user tg %s", tg_id)


# ================= ПЛАТЕЖИ =================

@router.callback_query(F.data == "ad:pay")
async def ad_pay(call: CallbackQuery, state: FSMContext, session):
    if not await guard(call, session):
        return
    await call.answer()
    pend = await psvc.pending_payments(session)
    recent = await psvc.recent_payments(session, 10)
    rows = [[(f"💳 #{p.id} · {fmt_money(p.amount)} ₽", f"adp:view:{p.id}")] for p in pend[:12]]
    if not rows:
        rows = [[("✅ Очередь пуста", "ad:pay")]]
    rows.append([("🔄 ОБНОВИТЬ", "ad:pay")])
    rows.append([("🔙 В АДМИНКУ", "ad:home")])
    lines = [f"<b>⏳ #{p.id} — {fmt_money(p.amount)} ₽ — user {p.user_id} — "
             f"{p.created_at.strftime('%d.%m %H:%M')}</b>" for p in pend[:12]]
    hist = [f"<b>{p.id}. {fmt_money(p.amount)} ₽ — {p.status} — user {p.user_id}</b>"
            for p in recent]
    text = (f"💳 <b>ПЛАТЕЖИ (PENDING: {len(pend)})</b>\n\n{LINE}\n\n" +
            ("\n".join(lines) or "<b>Нет заявок на проверке ✅</b>") +
            "\n\n📜 <b>Последние 10:</b>\n" + ("\n".join(hist) or "<b>пусто</b>"))
    await show(call, session, text=text, kb=inl(rows), banner="ADMIN", state=state)


async def payment_card(call, state, session, pid: int):
    p = await session.get(Payment, pid)
    if p is None:
        await call.answer("Не найдено", show_alert=True)
        return
    u = await usvc.get_user_by_id(session, p.user_id)
    text = f"""💳 <b>ЗАЯВКА НА ПОПОЛНЕНИЕ #{p.id}</b>

{LINE}

👤 <b>Пользователь: {u.nickname if u else '—'} (tg {u.tg_id if u else '?'})</b>
🆔 <b>ID пользователя: {p.user_id}</b>
💰 <b>Сумма: {fmt_money(p.amount)} ₽</b>
📅 <b>Дата: {p.created_at.strftime('%d.%m.%Y %H:%M')}</b>
📌 <b>Статус: {p.status}</b>"""
    if p.status == "PENDING":
        kb = inl([
            [("✅ ПОДТВЕРДИТЬ", f"adp:ok:{p.id}")],
            [("❌ ОТКЛОНИТЬ", f"adp:no:{p.id}")],
            [("🔙 К ПЛАТЕЖАМ", "ad:pay"), ("🏠 В АДМИНКУ", "ad:home")],
        ])
    else:
        kb = inl([[("🔙 К ПЛАТЕЖАМ", "ad:pay")], ("🏠 В АДМИНКУ", "ad:home")])
    await show(call, session, text=text, kb=kb, banner="ADMIN", state=state)


@router.callback_query(F.data.startswith("adp:view:"))
async def adp_view(call: CallbackQuery, state: FSMContext, session):
    if not await guard(call, session):
        return
    await call.answer()
    await payment_card(call, state, session, int(call.data.split(":")[2]))


@router.callback_query(F.data.startswith("adp:ok:"))
async def adp_ok(call: CallbackQuery, state: FSMContext, session):
    if not await guard(call, session):
        return
    pid = int(call.data.split(":")[2])
    p = await session.get(Payment, pid)
    if p is None:
        await call.answer("Не найдено", show_alert=True)
        return
    ok = await psvc.approve_payment(session, p)
    if not ok:
        await call.answer("Платёж уже обработан", show_alert=True)
        await ad_pay(call, state, session)
        return
    u = await usvc.get_user_by_id(session, p.user_id)
    await catalog.log_admin_action(session, call.from_user.id, "APPROVE_PAYMENT",
                                   f"payment:{p.id}", "PENDING", "COMPLETED")
    if u:
        await notify_user(call.bot, u.tg_id,
                          f"✅ <b>ПОПОЛНЕНИЕ ПОДТВЕРЖДЕНО!</b>\n\n{LINE}\n\n"
                          f"💰 <b>Зачислено: {fmt_money(p.amount)} ₽</b>\n"
                          f"💵 <b>Доступно: {fmt_money(u.available_balance)} ₽</b>\n\n"
                          "<b>Спасибо за пользование Playerok! 🛡</b>")
    await call.answer("✅ Подтверждено")
    await ad_pay(call, state, session)


@router.callback_query(F.data.startswith("adp:no:"))
async def adp_no(call: CallbackQuery, state: FSMContext, session):
    if not await guard(call, session):
        return
    pid = int(call.data.split(":")[2])
    await call.answer()
    await state.set_state(AdminStates.pay_note)
    await state.update_data(pay_note={"pid": pid})
    await show(call, session,
               text=f"❌ <b>ОТКЛОНЕНИЕ ПЛАТЕЖА #{pid}</b>\n\n{LINE}\n\n"
                    "<b>Отправьте причину отклонения (или «пропустить»):</b>",
               kb=inl([[("💬 ПРОПУСТИТЬ", f"adp:no_skip:{pid}")],
                       [("🔙 НАЗАД", f"adp:view:{pid}")]]), state=state)


@router.callback_query(F.data.startswith("adp:no_skip:"))
async def adp_no_skip(call: CallbackQuery, state: FSMContext, session):
    if not await guard(call, session):
        return
    pid = int(call.data.split(":")[2])
    await reject_payment_apply(call, state, session, pid, "")


@router.message(AdminStates.pay_note, F.text)
async def adp_no_note(message: Message, state: FSMContext, session):
    if not await guard(message, session):
        return
    data = await state.get_data()
    pid = data.get("pay_note", {}).get("pid")
    if not pid:
        await state.clear()
        return
    await reject_payment_apply(message, state, session, pid, message.text.strip(), msg=True)


async def reject_payment_apply(event, state, session, pid, note, msg=False):
    p = await session.get(Payment, pid)
    if p is None:
        if isinstance(event, CallbackQuery):
            await event.answer("Не найдено", show_alert=True)
        return
    ok = await psvc.reject_payment(session, p, note)
    if ok:
        u = await usvc.get_user_by_id(session, p.user_id)
        await catalog.log_admin_action(session, event.from_user.id, "REJECT_PAYMENT",
                                       f"payment:{p.id}", "PENDING", "REJECTED")
        if u:
            await notify_user(event.bot, u.tg_id,
                              f"❌ <b>ПОПОЛНЕНИЕ ОТКЛОНЕНО</b>\n\n{LINE}\n\n"
                              f"💰 <b>Заявка: {fmt_money(p.amount)} ₽ (#{p.id})</b>\n\n"
                              + (f"📝 <b>Причина: {esc(note[:300])}</b>\n\n" if note else
                                 "<b>Если считаете ошибкой — напишите в поддержку. 🆘</b>"))
    await state.clear()
    if msg:
        await event.answer("✅ Платёж отклонён", parse_mode="HTML")
    else:
        await event.answer("✅ Отклонено")
        await ad_pay(event, state, session)


# ================= ВЫВОДЫ =================

@router.callback_query(F.data == "ad:wdr")
async def ad_wdr(call: CallbackQuery, state: FSMContext, session):
    if not await guard(call, session):
        return
    await call.answer()
    pend = await psvc.pending_withdrawals(session)
    recent = await psvc.recent_withdrawals(session, 10)
    rows = [[(f"💸 #{w.id} · {fmt_money(w.amount)} ₽ · {w.status}", f"adw:view:{w.id}")]
            for w in pend[:12]]
    if not rows:
        rows = [[("✅ Очередь пуста", "ad:wdr")]]
    rows.append([("🔄 ОБНОВИТЬ", "ad:wdr")])
    rows.append([("🔙 В АДМИНКУ", "ad:home")])
    lines = [f"<b>{'⏳' if w.status == 'PENDING' else '📌'} #{w.id} — {fmt_money(w.amount)} ₽ — "
             f"user {w.user_id} — {w.status}</b>" for w in pend[:12]]
    hist = [f"<b>{w.id}. {fmt_money(w.amount)} ₽ — {w.status}</b>" for w in recent]
    text = (f"💸 <b>ВЫВОДЫ (в очереди: {len(pend)})</b>\n\n{LINE}\n\n" +
            ("\n".join(lines) or "<b>Нет заявок ✅</b>") +
            "\n\n📜 <b>История:</b>\n" + ("\n".join(hist) or "<b>пусто</b>"))
    await show(call, session, text=text, kb=inl(rows), banner="ADMIN", state=state)


async def withdrawal_card(call, state, session, wid: int):
    w = await session.get(Withdrawal, wid)
    if w is None:
        await call.answer("Не найдено", show_alert=True)
        return
    u = await usvc.get_user_by_id(session, w.user_id)
    text = f"""💸 <b>ЗАЯВКА НА ВЫВОД #{w.id}</b>

{LINE}

👤 <b>Пользователь: {u.nickname if u else '—'} (tg {u.tg_id if u else '?'})</b>
💰 <b>Сумма: {fmt_money(w.amount)} ₽</b>
🧾 <b>Реквизиты:</b>
<b>{esc(w.details[:400])}</b>
📅 <b>Дата: {w.created_at.strftime('%d.%m.%Y %H:%M')}</b>
📌 <b>Статус: {w.status}</b>"""
    if w.status in ("PENDING", "APPROVED"):
        kb = inl([
            [("✅ ВЫПЛАТА ПРОИЗВЕДЕНА", f"adw:done:{w.id}")],
            [("❌ ОТКЛОНИТЬ (вернуть баланс)", f"adw:no:{w.id}")],
            [("🔙 К ВЫВОДАМ", "ad:wdr"), ("🏠 В АДМИНКУ", "ad:home")],
        ])
    else:
        kb = inl([[("🔙 К ВЫВОДАМ", "ad:wdr")], ("🏠 В АДМИНКУ", "ad:home")])
    await show(call, session, text=text, kb=kb, banner="ADMIN", state=state)


@router.callback_query(F.data.startswith("adw:view:"))
async def adw_view(call: CallbackQuery, state: FSMContext, session):
    if not await guard(call, session):
        return
    await call.answer()
    await withdrawal_card(call, state, session, int(call.data.split(":")[2]))


@router.callback_query(F.data.startswith("adw:done:"))
async def adw_done(call: CallbackQuery, state: FSMContext, session):
    if not await guard(call, session):
        return
    wid = int(call.data.split(":")[2])
    w = await session.get(Withdrawal, wid)
    if w is None:
        await call.answer("Не найдено", show_alert=True)
        return
    ok = await psvc.complete_withdrawal(session, w)
    if ok:
        u = await usvc.get_user_by_id(session, w.user_id)
        await catalog.log_admin_action(session, call.from_user.id, "COMPLETE_WITHDRAWAL",
                                       f"withdrawal:{w.id}", "PENDING", "COMPLETED")
        if u:
            await notify_user(call.bot, u.tg_id,
                              f"✅ <b>ВЫВОД ЗАВЕРШЁН!</b>\n\n{LINE}\n\n"
                              f"💸 <b>Переведено: {fmt_money(w.amount)} ₽</b>\n\n"
                              "<b>Средства отправлены по указанным реквизитам. 💙</b>")
        await call.answer("✅ Готово")
    else:
        await call.answer("Уже обработано", show_alert=True)
    await ad_wdr(call, state, session)


@router.callback_query(F.data.startswith("adw:no:"))
async def adw_no(call: CallbackQuery, state: FSMContext, session):
    if not await guard(call, session):
        return
    wid = int(call.data.split(":")[2])
    await call.answer()
    await state.set_state(AdminStates.wdr_note)
    await state.update_data(wdr_note={"wid": wid})
    await show(call, session,
               text=f"❌ <b>ОТКЛОНЕНИЕ ВЫВОДА #{wid}</b>\n\n{LINE}\n\n"
                    "<b>Сумма вернётся на доступный баланс пользователя.</b>\n\n"
                    "<b>Отправьте причину (или используйте кнопку «пропустить»):</b>",
               kb=inl([[("💬 ПРОПУСТИТЬ ПРИЧИНУ", f"adw:no_skip:{wid}")],
                       [("🔙 НАЗАД", f"adw:view:{wid}")]]), state=state)


@router.callback_query(F.data.startswith("adw:no_skip:"))
async def adw_no_skip(call: CallbackQuery, state: FSMContext, session):
    if not await guard(call, session):
        return
    wid = int(call.data.split(":")[2])
    await reject_withdrawal_apply(call, state, session, wid, "")


@router.message(AdminStates.wdr_note, F.text)
async def adw_no_note(message: Message, state: FSMContext, session):
    if not await guard(message, session):
        return
    data = await state.get_data()
    wid = data.get("wdr_note", {}).get("wid")
    if not wid:
        await state.clear()
        return
    await reject_withdrawal_apply(message, state, session, wid, message.text.strip(), msg=True)


async def reject_withdrawal_apply(event, state, session, wid, note, msg=False):
    w = await session.get(Withdrawal, wid)
    if w is None:
        if isinstance(event, CallbackQuery):
            await event.answer("Не найдено", show_alert=True)
        return
    ok = await psvc.reject_withdrawal(session, w, note)
    if ok:
        u = await usvc.get_user_by_id(session, w.user_id)
        await catalog.log_admin_action(session, event.from_user.id, "REJECT_WITHDRAWAL",
                                       f"withdrawal:{w.id}", "PENDING", "REJECTED")
        if u:
            await notify_user(event.bot, u.tg_id,
                              f"❌ <b>ЗАЯВКА НА ВЫВОД #{w.id} ОТКЛОНЕНА</b>\n\n{LINE}\n\n"
                              f"🔄 <b>Возврат на баланс: {fmt_money(w.amount)} ₽</b>\n\n"
                              + (f"📝 <b>Причина: {esc(note[:300])}</b>" if note else
                                 "<b>По вопросам — поддержка. 🆘</b>"))
    await state.clear()
    if msg:
        await event.answer("✅ Заявка отклонена, средства возвращены", parse_mode="HTML")
    else:
        await event.answer("✅ Отклонено")
        await ad_wdr(event, state, session)


# ================= СПОРЫ =================

@router.callback_query(F.data == "ad:disp")
async def ad_disp(call: CallbackQuery, state: FSMContext, session):
    if not await guard(call, session):
        return
    await call.answer()
    items = await dsvc.pending_disputes(session)
    rows = [[(f"⚖️ #{d.id} · сделка #{d.deal_id}", f"add:view:{d.id}")] for d in items[:15]]
    if not rows:
        rows = [[("✅ Споров нет", "ad:disp")]]
    rows.append([("🔄 ОБНОВИТЬ", "ad:disp")])
    rows.append([("🔙 В АДМИНКУ", "ad:home")])
    lines = [f"<b>⚖️ #{d.id} — {esc(d.reason)} — deal {d.deal_id} — user {d.user_id}</b>"
             for d in items[:15]]
    text = (f"⚖️ <b>СПОРЫ НА РАССМОТРЕНИИ ({len(items)})</b>\n\n{LINE}\n\n" +
            ("\n".join(lines) or "<b>Открытых споров нет ✅</b>"))
    await show(call, session, text=text, kb=inl(rows), banner="ADMIN", state=state)


async def dispute_card(call, state, session, did: int, note_prompt: str | None = None):
    d = await session.get(Dispute, did)
    if d is None:
        await call.answer("Не найдено", show_alert=True)
        return
    deal = await dsvc.get_deal(session, d.deal_id)
    user = await usvc.get_user_by_id(session, d.user_id)
    text = f"""⚖️ <b>СПОР #{d.id}</b>

{LINE}

🆔 <b>Сделка: #{deal.code if deal else '?'}</b>
💰 <b>Сумма: {fmt_money(deal.price) if deal else '?'} ₽</b>
📌 <b>Статус сделки: {status_line_plain(deal.status) if deal else '?'}</b>
👤 <b>От кого: {user.nickname if user else '—'} (tg {user.tg_id if user else '?'})</b>
📝 <b>Причина: {esc(d.reason)}</b>

<b>Описание:</b>
<b>{esc(d.description[:900])}</b>

📅 <b>Создан: {d.created_at.strftime('%d.%m.%Y %H:%M')}</b>
🎯 <b>Статус: {d.status}</b>"""
    if note_prompt:
        text += f"\n\n✏️ <b>Отправьте комментарий к решению (или пропустите):</b>"
        side = "seller" if note_prompt == "seller" else "buyer"
        kb = inl([[("💬 БЕЗ КОММЕНТАРИЯ", f"add:fin:{d.id}:{side}:skip")]])
    elif d.status == "OPEN":
        kb = inl([
            [("✅ В ПОЛЬЗУ ПРОДАВЦА", f"add:sell:{d.id}")],
            [("🔄 В ПОЛЬЗУ ПОКУПАТЕЛЯ (возврат)", f"add:buy:{d.id}")],
            [("🔙 К СПОРАМ", "ad:disp"), ("🏠 В АДМИНКУ", "ad:home")],
        ])
    else:
        kb = inl([[("🔙 К СПОРАМ", "ad:disp")], ("🏠 В АДМИНКУ", "ad:home")])
    await show(call, session, text=text, kb=kb, banner="ADMIN", state=state)


@router.callback_query(F.data.startswith("add:view:"))
async def add_view(call: CallbackQuery, state: FSMContext, session):
    if not await guard(call, session):
        return
    await call.answer()
    await dispute_card(call, state, session, int(call.data.split(":")[2]))


@router.callback_query(F.data.startswith("add:sell:"))
async def add_sell(call: CallbackQuery, state: FSMContext, session):
    if not await guard(call, session):
        return
    did = int(call.data.split(":")[2])
    await call.answer()
    await state.set_state(AdminStates.disp_seller_note)
    await state.update_data(disp_note={"did": did, "winner": "seller"})
    await dispute_card(call, state, session, did, note_prompt="seller")


@router.callback_query(F.data.startswith("add:buy:"))
async def add_buy(call: CallbackQuery, state: FSMContext, session):
    if not await guard(call, session):
        return
    did = int(call.data.split(":")[2])
    await call.answer()
    await state.set_state(AdminStates.disp_buyer_note)
    await state.update_data(disp_note={"did": did, "winner": "buyer"})
    await dispute_card(call, state, session, did, note_prompt="buyer")


@router.callback_query(F.data.startswith("add:fin:"))
async def add_fin_skip(call: CallbackQuery, state: FSMContext, session):
    if not await guard(call, session):
        return
    parts = call.data.split(":")
    did, side, skip = int(parts[2]), parts[3], parts[4]
    await resolve_dispute_apply(call, state, session, did, side, "", cb=True)


@router.message(AdminStates.disp_seller_note, F.text)
@router.message(AdminStates.disp_buyer_note, F.text)
async def add_note_input(message: Message, state: FSMContext, session):
    if not await guard(message, session):
        return
    data = await state.get_data()
    dn = data.get("disp_note", {})
    if not dn:
        await state.clear()
        return
    await resolve_dispute_apply(message, state, session, dn["did"], dn["winner"],
                                message.text.strip(), cb=False)


async def resolve_dispute_apply(event, state, session, did, winner, note, cb: bool):
    d = await session.get(Dispute, did)
    if d is None:
        if cb:
            await event.answer("Не найдено", show_alert=True)
        return
    ok = await dsvc.resolve_dispute(session, d, winner, note)
    if ok:
        deal = await dsvc.get_deal(session, d.deal_id)
        await catalog.log_admin_action(session, event.from_user.id, "RESOLVE_DISPUTE",
                                       f"dispute:{d.id}", "OPEN",
                                       f"WON_{winner.upper()}")
        # уведомления участникам
        if deal:
            seller = await usvc.get_user_by_id(session, deal.seller_id)
            buyer = await usvc.get_user_by_id(session, deal.buyer_id) if deal.buyer_id else None
            won = "продавца" if winner == "seller" else "покупателя"
            for u, mine in ((seller, winner == "seller"), (buyer, winner == "buyer")):
                if not u:
                    continue
                head = ("✅ <b>СПОР РЕШЁН В ВАШУ ПОЛЬЗУ</b>" if mine
                        else "❌ <b>СПОР РЕШЁН НЕ В ВАШУ ПОЛЬЗУ</b>")
                await notify_user(event.bot, u.tg_id,
                                  f"⚖️ <b>СПОР #{d.id} ПО СДЕЛКЕ #{deal.code}</b>\n\n{LINE}\n\n"
                                  f"{head}\n\n<b>Решение в пользу: {won}.</b>\n\n"
                                  + (f"📝 <b>Комментарий: {esc(note[:300])}</b>" if note else
                                     "<b>По вопросам — @PIayerokSupports 🆘</b>"))
    await state.clear()
    if cb:
        await event.answer("✅ Спор решён")
        await ad_disp(event, state, session)
    else:
        await event.answer("✅ Спор решён.", parse_mode="HTML")
        await ad_disp_cb_via_message(event, state, session)


async def ad_disp_cb_via_message(message, state, session):
    items = await dsvc.pending_disputes(session)
    rows = [[(f"⚖️ #{d.id}", f"add:view:{d.id}")] for d in items[:15]]
    rows.append([("🔙 В АДМИНКУ", "ad:home")])
    await message.answer(f"⚖️ <b>СПОРЫ ({len(items)})</b>", reply_markup=inl(rows),
                         parse_mode="HTML")


# ================= ОТЗЫВЫ =================

@router.callback_query(F.data == "ad:rev")
async def ad_rev(call: CallbackQuery, state: FSMContext, session):
    if not await guard(call, session):
        return
    await call.answer()
    stmt = select(Review).order_by(Review.created_at.desc()).limit(15)
    items = (await session.execute(stmt)).scalars().all()
    total = (await session.execute(select(func.count(Review.id)))).scalar_one()
    if not items:
        await show(call, session, text=f"⭐ <b>ОТЗЫВОВ ПОКА НЕТ</b>\n\n{LINE}",
                   kb=inl([[("🔙 В АДМИНКУ", "ad:home")]]), banner="ADMIN", state=state)
        return
    lines = []
    for r in items:
        a = await usvc.get_user_by_id(session, r.author_id)
        t = await usvc.get_user_by_id(session, r.target_id)
        lines.append(f"<b>{'⭐' * r.stars} {a.nickname if a else '?'} → "
                     f"{t.nickname if t else '?'}: {esc((r.text or '')[:80])}</b>")
    text = (f"⭐ <b>ОТЗЫВЫ (всего {total})</b>\n\n{LINE}\n\n" + "\n".join(lines))
    await show(call, session, text=text,
               kb=inl([[("🔄 ОБНОВИТЬ", "ad:rev")], ("🔙 В АДМИНКУ", "ad:home")]),
               banner="ADMIN", state=state)


# ================= ТИКЕТЫ =================

@router.callback_query(F.data == "ad:tickets")
async def ad_tickets(call: CallbackQuery, state: FSMContext, session):
    if not await guard(call, session):
        return
    await call.answer()
    stmt = select(SupportTicket).where(SupportTicket.status == "OPEN") \
        .order_by(SupportTicket.created_at.desc()).limit(15)
    items = (await session.execute(stmt)).scalars().all()
    rows = [[(f"🎫 #{t.id} · user {t.user_id}", f"adt:view:{t.id}")] for t in items[:12]]
    if not rows:
        rows = [[("✅ Открытых тикетов нет", "ad:tickets")]]
    rows.append([("🔄 ОБНОВИТЬ", "ad:tickets")])
    rows.append([("🔙 В АДМИНКУ", "ad:home")])
    lines = [f"<b>🎫 #{t.id} — user {t.user_id}: {esc((t.message or '')[:70])}</b>"
             for t in items[:15]]
    text = (f"🎫 <b>ТИКЕТЫ ПОДДЕРЖКИ ({len(items)})</b>\n\n{LINE}\n\n" +
            ("\n".join(lines) or "<b>Пусто ✅</b>"))
    await show(call, session, text=text, kb=inl(rows), banner="ADMIN", state=state)


@router.callback_query(F.data.startswith("adt:view:"))
async def adt_view(call: CallbackQuery, state: FSMContext, session):
    if not await guard(call, session):
        return
    tid = int(call.data.split(":")[2])
    t = await session.get(SupportTicket, tid)
    await call.answer()
    if t is None:
        await ad_tickets(call, state, session)
        return
    u = await usvc.get_user_by_id(session, t.user_id)
    text = f"""🎫 <b>ТИКЕТ #{t.id}</b>

{LINE}

👤 <b>От: {u.nickname if u else '—'} (tg {u.tg_id if u else '?'})</b>
📅 <b>{t.created_at.strftime('%d.%m.%Y %H:%M')}</b>
📌 <b>Статус: {t.status}</b>

<b>{esc(t.message[:1500])}</b>"""
    kb = inl([
        [("✅ ЗАКРЫТЬ ТИКЕТ", f"adt:close:{t.id}")],
        [("💬 ОТВЕТИТЬ ПОЛЬЗОВАТЕЛЮ", f"adt:answer:{t.id}")],
        [("🔙 К ТИКЕТАМ", "ad:tickets"), ("🏠 В АДМИНКУ", "ad:home")],
    ])
    await show(call, session, text=text, kb=kb, banner="ADMIN", state=state)


@router.callback_query(F.data.startswith("adt:close:"))
async def adt_close(call: CallbackQuery, state: FSMContext, session):
    if not await guard(call, session):
        return
    tid = int(call.data.split(":")[2])
    t = await session.get(SupportTicket, tid)
    if t is not None and t.status != "CLOSED":
        t.status = "CLOSED"
        await session.commit()
        await catalog.log_admin_action(session, call.from_user.id, "CLOSE_TICKET",
                                       f"ticket:{t.id}", "OPEN", "CLOSED")
    await call.answer("✅ Закрыт")
    await ad_tickets(call, state, session)


@router.callback_query(F.data.startswith("adt:answer:"))
async def adt_answer(call: CallbackQuery, state: FSMContext, session):
    if not await guard(call, session):
        return
    tid = int(call.data.split(":")[2])
    await call.answer()
    await state.set_state(AdminStates.mod_note)  # переиспользуем состояние ввода текста
    await state.update_data(ticket_answer={"tid": tid})
    await show(call, session,
               text=f"💬 <b>ОТВЕТ НА ТИКЕТ #{tid}</b>\n\n{LINE}\n\n"
                    "<b>Отправьте текст ответа — он будет доставлен пользователю:</b>",
               kb=inl([[("🔙 НАЗАД", f"adt:view:{tid}")]]), state=state)


# ================= ЛОГИ =================

@router.callback_query(F.data == "ad:logs")
async def ad_logs(call: CallbackQuery, state: FSMContext, session):
    if not await guard(call, session):
        return
    await call.answer()
    from bot.db.models import AdminAction
    stmt = select(AdminAction).order_by(AdminAction.timestamp.desc()).limit(20)
    items = (await session.execute(stmt)).scalars().all()
    lines = [f"<b>{a.timestamp.strftime('%d.%m %H:%M')} · {a.action} · {a.object}"
             f"{(' : ' + str(a.old_value) + '→' + str(a.new_value)) if (a.old_value or a.new_value) else ''}</b>"
             for a in items]
    text = (f"📜 <b>ЛОГИ АДМИН-ДЕЙСТВИЙ</b>\n\n{LINE}\n\n" +
            ("\n".join(lines) or "<b>Пусто</b>"))
    await show(call, session, text=text,
               kb=inl([[("🔄 ОБНОВИТЬ", "ad:logs")], ("🔙 В АДМИНКУ", "ad:home")]),
               banner="ADMIN", state=state)
