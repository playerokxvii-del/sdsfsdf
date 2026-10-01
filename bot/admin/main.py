"""Админ-панель Playerok: статистика, пользователи, сделки, платежи, выводы,
споры, модерация, отзывы, категории, рассылка, настройки, баннеры."""
from __future__ import annotations

import logging
from decimal import Decimal

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select

from bot.config.settings import get_config
from bot.db.base import get_setting, set_setting
from bot.db.models import (
    BANNER_SECTIONS, AdminAction, Banner, Category, Dispute, Game, NewsPost,
    Payment, Review, SupportTicket, User, Withdrawal,
)
from bot.filters.access import is_admin_id
from bot.handlers.deal_create import esc
from bot.keyboard.kbs import inl
from bot.services import catalog, deals as dsvc, payments as psvc, users as usvc
from bot.states.states import AdminStates
from bot.utils.renderer import render_screen, show
from bot.utils.texts import LINE, fmt_money, rating_text, status_line_plain

log = logging.getLogger("playerok.admin")
router = Router(name="admin")


async def admins_list(session) -> list[int]:
    ids = [get_config().OWNER_ID]
    st = await get_setting(session, "admin_ids", "")
    ids += [int(x) for x in st.replace(" ", "").split(",") if x.isdigit()]
    return list(dict.fromkeys(ids))


async def guard(call_or_message, session) -> bool:
    tg_id = call_or_message.from_user.id if call_or_message.from_user else None
    ok = await is_admin_id(tg_id)
    if not ok:
        try:
            if isinstance(call_or_message, CallbackQuery):
                await call_or_message.answer("⛔ Доступ запрещён", show_alert=True)
            else:
                await call_or_message.answer("⛔ <b>Доступ запрещён.</b>", parse_mode="HTML")
        except Exception:
            pass
    return ok


def admin_home_kb() -> object:
    return inl([
        [("📊 СТАТИСТИКА", "ad:stats"), ("👥 ПОЛЬЗОВАТЕЛИ", "ad:users")],
        [("🤝 СДЕЛКИ", "ad:deals"), ("🛡️ МОДЕРАЦИЯ", "ad:mod")],
        [("💳 ПЛАТЕЖИ", "ad:pay"), ("💸 ВЫВОДЫ", "ad:wdr")],
        [("⚖️ СПОРЫ", "ad:disp"), ("⭐ ОТЗЫВЫ", "ad:rev")],
        [("📂 КАТЕГОРИИ", "ad:cats"), ("📢 РАССЫЛКА", "ad:bcast")],
        [("📰 НОВОСТИ", "ad:news"), ("🖼 БАННЕРЫ", "ad:banners")],
        [("🎫 ТИКЕТЫ", "ad:tickets"), ("📜 ЛОГИ", "ad:logs")],
        [("⚙️ НАСТРОЙКИ", "ad:settings")],
        [("🏠 В МЕНЮ БОТА", "nav:menu")],
    ])


@router.message(F.text == "/admin")
async def admin_cmd(message: Message, state: FSMContext, session):
    if not await guard(message, session):
        return
    await state.clear()
    await render_screen(message.bot, session, chat_id=message.chat.id,
                        text="🛠 <b>ПАНЕЛЬ АДМИНИСТРАТОРА PLAYEROK</b>\n\n" + LINE +
                             "\n\n<b>Выберите раздел 👇</b>",
                        kb=admin_home_kb(), banner_section="ADMIN", state=state)


@router.callback_query(F.data == "ad:home")
async def ad_home(call: CallbackQuery, state: FSMContext, session):
    if not await guard(call, session):
        return
    await call.answer()
    await show(call, session,
               text="🛠 <b>ПАНЕЛЬ АДМИНИСТРАТОРА PLAYEROK</b>\n\n" + LINE +
                    "\n\n<b>Выберите раздел 👇</b>",
               kb=admin_home_kb(), banner="ADMIN", state=state)


# ================= СТАТИСТИКА =================

@router.callback_query(F.data == "ad:stats")
async def ad_stats(call: CallbackQuery, state: FSMContext, session):
    if not await guard(call, session):
        return
    s = await catalog.stats(session)
    text = f"""📊 <b>СТАТИСТИКА PLAYEROK</b>

{LINE}

👥 <b>Пользователей: {s['users_total']}</b>
🆕 <b>За сегодня: {s['users_today']}</b>

🤝 <b>Активных сделок: {s['deals_active']}</b>
✅ <b>Завершено: {s['deals_completed']}</b>
💰 <b>Оборот: {fmt_money(s['turnover'])} ₽</b>
🧾 <b>Комиссия сервиса: {fmt_money(s['commission'])} ₽</b>

💳 <b>Платежи на проверке: {s['pend_payments']}</b>
💸 <b>Выводы в очереди: {s['pend_withdraws']}</b>
⚖️ <b>Открытые споры: {s['open_disputes']}</b>

🏦 <b>Балансы пользователей: {fmt_money(s['balance_sum'])} ₽</b>
🔒 <b>Заморожено: {fmt_money(s['frozen_sum'])} ₽</b>"""
    await call.answer()
    await show(call, session, text=text,
               kb=inl([[("🔄 ОБНОВИТЬ", "ad:stats")], ("🔙 В АДМИНКУ", "ad:home")]),
               banner="ADMIN", state=state)


# ================= ПОЛЬЗОВАТЕЛИ =================

@router.callback_query(F.data == "ad:users")
async def ad_users(call: CallbackQuery, state: FSMContext, session):
    if not await guard(call, session):
        return
    await call.answer()
    await state.set_state(AdminStates.user_search)
    top = (await session.execute(
        select(User).order_by(User.created_at.desc()).limit(10))).scalars().all()
    lines = []
    for u in top:
        lines.append(f"<b>👤 {u.nickname or '—'} · tg {u.tg_id}\n"
                     f"   💰 {fmt_money(u.available_balance)} ₽ · 🤝 {u.deals_created}"
                     f"{' · 🚫 ЗАБЛОКИРОВАН' if u.blocked else ''}</b> "
                     f"[<b>ID:{u.id}</b>]")
    text = (f"👥 <b>ПОЛЬЗОВАТЕЛИ</b>\n\n{LINE}\n\n"
            "<b>🔎 Поиск: отправьте Telegram ID, username или никнейм.</b>\n\n"
            "<b>Последние 10:</b>\n\n" + ("\n\n".join(lines) or "<b>пусто</b>"))
    rows = [[(f"👤 {u.nickname or str(u.tg_id)}", f"adu:view:{u.id}")] for u in top[:8]]
    rows.append([("🔙 В АДМИНКУ", "ad:home")])
    await show(call, session, text=text, kb=inl(rows), banner="ADMIN", state=state)


@router.message(AdminStates.user_search, F.text)
async def ad_user_search(message: Message, state: FSMContext, session):
    if not await guard(message, session):
        return
    q = message.text.strip()
    found = await catalog.search_users(session, q)
    if not found:
        await message.answer("🔎 <b>Никого не нашли по запросу.</b>\n\n<b>Попробуйте снова:</b>",
                             parse_mode="HTML")
        return
    rows = [[(f"👤 {u.nickname or u.username or str(u.tg_id)}", f"adu:view:{u.id}")]
            for u in found[:15]]
    rows.append([("🔙 К ПОЛЬЗОВАТЕЛЯМ", "ad:users"), ("🏠 В АДМИНКУ", "ad:home")])
    text = (f"🔎 <b>РЕЗУЛЬТАТЫ ПОИСКА ({len(found)})</b>\n\n{LINE}\n\n" +
            "\n".join(f"<b>• {u.nickname or '—'} — tg {u.tg_id}</b>" for u in found[:15]))
    await message.answer(text, reply_markup=inl(rows), parse_mode="HTML")
    await state.clear()


async def user_card(event, session, state, uid: int):
    u = await usvc.get_user_by_id(session, uid)
    if u is None:
        await show(event, session, text="❌ <b>Пользователь не найден</b>",
                   kb=inl([[("🔙 НАЗАД", "ad:users")]]), state=state)
        return
    from sqlalchemy import func
    from bot.db.models import Deal
    created = (await session.execute(
        select(func.count(Deal.id)).where(Deal.seller_id == u.id))).scalar_one()
    completed = (await session.execute(
        select(func.count(Deal.id)).where(
            ((Deal.seller_id == u.id) | (Deal.buyer_id == u.id)),
            Deal.status == "COMPLETED"))).scalar_one()
    text = f"""👤 <b>ПРОФИЛЬ ПОЛЬЗОВАТЕЛЯ</b>

{LINE}

🆔 <b>ID: {u.id}</b>
📱 <b>TG ID: {u.tg_id}</b>
🔗 <b>Username: @{esc(u.username or '—')}</b>
👤 <b>Никнейм: {esc(u.nickname or '—')}</b>
📞 <b>Телефон: {esc(u.phone or '—')}</b>
💰 <b>Баланс: {fmt_money(u.available_balance)} ₽</b>
🔒 <b>Frozen: {fmt_money(u.frozen_balance)} ₽</b>
🤝 <b>Сделок создано: {created}</b>
✅ <b>Завершено: {completed}</b>
⭐ <b>Рейтинг: {rating_text(u)}</b>
📅 <b>Регистрация: {u.created_at.strftime('%d.%m.%Y %H:%M')}</b>
📌 <b>Статус: {'🚫 Заблокирован' if u.blocked else '✅ Активен'}</b>"""
    kb = inl([
        [("🚫 ЗАБЛОКИРОВАТЬ", f"adu:block:{u.id}"), ("🔓 РАЗБЛОКИРОВАТЬ", f"adu:unblock:{u.id}")],
        [("➕ ЗАЧИСЛИТЬ", f"adu:adj:{u.id}:plus"), ("➖ СПИСАТЬ", f"adu:adj:{u.id}:minus")],
        [("🔙 К ПОЛЬЗОВАТЕЛЯМ", "ad:users"), ("🏠 В АДМИНКУ", "ad:home")],
    ])
    await show(event, session, text=text, kb=kb, banner="ADMIN", state=state)


@router.callback_query(F.data.startswith("adu:view:"))
async def ad_user_view(call: CallbackQuery, state: FSMContext, session):
    if not await guard(call, session):
        return
    await call.answer()
    await user_card(call, session, state, int(call.data.split(":")[2]))


@router.callback_query(F.data.startswith("adu:block:"))
async def ad_user_block(call: CallbackQuery, state: FSMContext, session):
    if not await guard(call, session):
        return
    uid = int(call.data.split(":")[2])
    u = await usvc.get_user_by_id(session, uid)
    if u is None:
        await call.answer("Не найден", show_alert=True)
        return
    old = "blocked" if u.blocked else "active"
    u.blocked = True
    await session.commit()
    await catalog.log_admin_action(session, call.from_user.id, "BLOCK_USER",
                                   f"user:{u.id}", old, "blocked")
    await call.answer("🚫 Пользователь заблокирован")
    await user_card(call, session, state, uid)


@router.callback_query(F.data.startswith("adu:unblock:"))
async def ad_user_unblock(call: CallbackQuery, state: FSMContext, session):
    if not await guard(call, session):
        return
    uid = int(call.data.split(":")[2])
    u = await usvc.get_user_by_id(session, uid)
    if u is None:
        await call.answer("Не найден", show_alert=True)
        return
    old = "blocked" if u.blocked else "active"
    u.blocked = False
    await session.commit()
    await catalog.log_admin_action(session, call.from_user.id, "UNBLOCK_USER",
                                   f"user:{u.id}", old, "active")
    await call.answer("🔓 Пользователь разблокирован")
    await user_card(call, session, state, uid)


@router.callback_query(F.data.startswith("adu:adj:"))
async def ad_user_adjust(call: CallbackQuery, state: FSMContext, session):
    if not await guard(call, session):
        return
    parts = call.data.split(":")
    uid, sign = int(parts[2]), parts[3]
    await call.answer()
    await state.set_state(AdminStates.user_adjust_amount)
    await state.update_data(adj={"uid": uid, "sign": sign})
    await show(call, session,
               text=f"⚖️ <b>КОРРЕКТИРОВКА БАЛАНСА</b>\n\n{LINE}\n\n"
                    f"<b>{'➕ Зачисляем' if sign == 'plus' else '➖ Списываем'} средства "
                    f"пользователю #{uid}.</b>\n\n<b>Отправьте сумму числом:</b>",
               kb=inl([[("❌ ОТМЕНА", f"adu:view:{uid}")]]), state=state)


@router.message(AdminStates.user_adjust_amount, F.text)
async def ad_user_adjust_apply(message: Message, state: FSMContext, session):
    if not await guard(message, session):
        return
    from bot.services.finance import add_available, sub_available, InsufficientFunds
    data = await state.get_data()
    adj = data.get("adj", {})
    try:
        amount = Decimal(message.text.strip().replace(",", "."))
        assert 0 < amount <= Decimal("100000000")
    except Exception:
        await message.answer("❌ <b>Некорректная сумма.</b>", parse_mode="HTML")
        return
    u = await usvc.get_user_by_id(session, adj.get("uid", 0))
    if u is None:
        await state.clear()
        await message.answer("❌ <b>Пользователь не найден.</b>", parse_mode="HTML")
        return
    try:
        if adj.get("sign") == "plus":
            await add_available(session, u.id, amount, "ADJUSTMENT",
                                comment="Ручная корректировка администратором")
        else:
            await sub_available(session, u.id, amount, "ADJUSTMENT",
                                comment="Ручная корректировка администратором")
        await session.commit()
    except InsufficientFunds:
        await session.rollback()
        await message.answer("⚠️ <b>Недостаточно средств на балансе пользователя.</b>",
                             parse_mode="HTML")
        return
    except Exception:
        await session.rollback()
        log.exception("adjust failed")
        await message.answer("⚠️ <b>Ошибка операции. Баланс не изменён.</b>", parse_mode="HTML")
        return
    await catalog.log_admin_action(session, message.from_user.id, "ADJUST_BALANCE",
                                   f"user:{u.id}", None, f"{adj.get('sign')} {amount}")
    await state.clear()
    await message.answer(f"✅ <b>Баланс пользователя #{u.id} обновлён на {fmt_money(amount)} ₽.</b>",
                         parse_mode="HTML")
    await user_card(message, session, state, u.id)


# ================= СДЕЛКИ =================

@router.callback_query(F.data == "ad:deals")
async def ad_deals(call: CallbackQuery, state: FSMContext, session):
    if not await guard(call, session):
        return
    from bot.db.models import Deal
    stmt = select(Deal).order_by(Deal.created_at.desc()).limit(15)
    items = (await session.execute(stmt)).scalars().all()
    await call.answer()
    if not items:
        await show(call, session, text="🤝 <b>СДЕЛОК ПОКА НЕТ</b>",
                   kb=inl([[("🔙 В АДМИНКУ", "ad:home")]]), banner="ADMIN", state=state)
        return
    lines = [f"<b>#{d.code} · {esc(d.title[:30])} · {fmt_money(d.price)} ₽ · "
             f"{status_line_plain(d.status)}</b>" for d in items]
    rows = [[(f"🆔 #{d.code}", f"deal:view:{d.id}")] for d in items[:12]]
    rows.append([("🔙 В АДМИНКУ", "ad:home")])
    await show(call, session,
               text=f"🤝 <b>ВСЕ СДЕЛКИ (последние {len(items)})</b>\n\n{LINE}\n\n" +
                    "\n".join(lines),
               kb=inl(rows), banner="ADMIN", state=state)


# ================= МОДЕРАЦИЯ =================

async def mod_list(call, state, session):
    queue = await dsvc.moderation_queue(session)
    if not queue:
        await show(call, session,
                   text=f"🛡 <b>МОДЕРАЦИЯ</b>\n\n{LINE}\n\n✅ <b>Очередь пуста — сделок на проверке нет.</b>",
                   kb=inl([[("🔄 ОБНОВИТЬ", "ad:mod")], ("🔙 В АДМИНКУ", "ad:home")]),
                   banner="ADMIN", state=state)
        return
    rows = [[(f"⏳ #{d.code} · {fmt_money(d.price)} ₽", f"adm:deal:{d.id}")] for d in queue[:15]]
    rows.append([("🔄 ОБНОВИТЬ", "ad:mod")])
    rows.append([("🔙 В АДМИНКУ", "ad:home")])
    lines = [f"<b>⏳ #{d.code} — {esc(d.title[:40])} — {fmt_money(d.price)} ₽</b>" for d in queue[:15]]
    await show(call, session,
               text=f"🛡 <b>МОДЕРАЦИЯ — ОЖИДАЮТ ПРОВЕРКИ ({len(queue)})</b>\n\n{LINE}\n\n" +
                    "\n".join(lines),
               kb=inl(rows), banner="ADMIN", state=state)


@router.callback_query(F.data == "ad:mod")
async def ad_mod(call: CallbackQuery, state: FSMContext, session):
    if not await guard(call, session):
        return
    await call.answer()
    await mod_list(call, state, session)


async def mod_deal_card(call, state, session, deal_id: int, note_prompt: bool = False):
    deal = await dsvc.get_deal(session, deal_id)
    if deal is None:
        await call.answer("Сделка не найдена", show_alert=True)
        return
    seller = await usvc.get_user_by_id(session, deal.seller_id)
    buyer = await usvc.get_user_by_id(session, deal.buyer_id) if deal.buyer_id else None
    cat = f"{deal.category.emoji} {deal.category.name}" if deal.category else "—"
    if deal.game:
        cat += f" · {deal.game.name}"
    text = f"""🛡 <b>СДЕЛКА НА МОДЕРАЦИЮ</b>

{LINE}

🆔 <b>ID сделки: #{deal.code}</b>
👤 <b>Продавец: {seller.nickname if seller else '—'} (tg {seller.tg_id if seller else '?'})</b>
👤 <b>Покупатель: {buyer.nickname if buyer else '—'} (tg {buyer.tg_id if buyer else '?'})</b>
📦 <b>Товар: {esc(deal.title[:200])}</b>
🎮 <b>Категория: {cat}</b>
🔗 <b>Ссылка: {esc(deal.link or '—')}</b>
💰 <b>Сумма: {fmt_money(deal.price)} ₽</b>
💸 <b>Комиссия: {fmt_money(deal.commission)} ₽</b>
🔒 <b>К выплате продавцу: {fmt_money(deal.seller_amount)} ₽</b>
📝 <b>Описание: {esc((deal.description or '—')[:300])}</b>

📌 <b>Статус: {status_line_plain(deal.status)}</b>"""
    if note_prompt:
        text += "\n\n✏️ <b>Отправьте комментарий/заметку к решению (или «пропустить»):</b>"
        kb = inl([[("💬 ПРОПУСТИТЬ ЗАМЕТКУ", f"adm:note_skip:{deal.id}")]])
    else:
        kb = inl([
            [("✅ ОДОБРИТЬ", f"adm:approve:{deal.id}")],
            [("❌ ОТКЛОНИТЬ", f"adm:reject:{deal.id}")],
            [("⚖️ В СПОР", f"adm:dispute:{deal.id}")],
            [("🔙 К МОДЕРАЦИИ", "ad:mod"), ("🏠 В АДМИНКУ", "ad:home")],
        ])
    await show(call, session, text=text, kb=kb, banner="ADMIN", state=state)


@router.callback_query(F.data.startswith("adm:deal:"))
async def adm_deal(call: CallbackQuery, state: FSMContext, session):
    if not await guard(call, session):
        return
    await call.answer()
    await mod_deal_card(call, state, session, int(call.data.split(":")[2]))


@router.callback_query(F.data.startswith("adm:approve:"))
async def adm_approve(call: CallbackQuery, state: FSMContext, session):
    if not await guard(call, session):
        return
    deal_id = int(call.data.split(":")[2])
    deal = await dsvc.get_deal(session, deal_id)
    if deal is None:
        await call.answer("Не найдено", show_alert=True)
        return
    ok = await dsvc.approve_deal(session, deal)
    if not ok:
        await call.answer("Сделка уже обработана", show_alert=True)
        await mod_list(call, state, session)
        return
    await catalog.log_admin_action(session, call.from_user.id, "APPROVE_DEAL",
                                   f"deal:{deal.code}", deal.status, "COMPLETED")
    # уведомления сторонам
    seller = await usvc.get_user_by_id(session, deal.seller_id)
    buyer = await usvc.get_user_by_id(session, deal.buyer_id) if deal.buyer_id else None
    if seller:
        try:
            await call.bot.send_message(
                seller.tg_id,
                f"✅ <b>СДЕЛКА #{deal.code} ОДОБРЕНА!</b>\n\n{LINE}\n\n"
                f"🔓 <b>Разморожено: {fmt_money(deal.seller_amount)} ₽</b>\n"
                f"💰 <b>Зачислено на баланс: {fmt_money(seller.available_balance)} ₽ доступно</b>\n\n"
                "<b>Теперь вы можете поставить ⭐ отзыв покупателю и вывести средства.</b>",
                parse_mode="HTML")
        except Exception:
            log.info("cannot notify seller about approval")
    if buyer:
        try:
            await call.bot.send_message(
                buyer.tg_id,
                f"✅ <b>СДЕЛКА #{deal.code} ЗАВЕРШЕНА</b>\n\n{LINE}\n\n"
                "<b>Проверка пройдена. Товар ваш — приятного пользования! 🎉</b>\n\n"
                "<b>Вы можете поставить ⭐ оценку продавцу.</b>",
                parse_mode="HTML")
        except Exception:
            log.info("cannot notify buyer about approval")
    await call.answer("✅ Одобрено")
    await mod_list(call, state, session)


@router.callback_query(F.data.startswith("adm:reject:"))
async def adm_reject(call: CallbackQuery, state: FSMContext, session):
    if not await guard(call, session):
        return
    deal_id = int(call.data.split(":")[2])
    await call.answer()
    await state.set_state(AdminStates.mod_note)
    await state.update_data(mod_note={"deal_id": deal_id, "action": "reject"})
    await mod_deal_card(call, state, session, deal_id, note_prompt=True)


@router.callback_query(F.data.startswith("adm:dispute:"))
async def adm_dispute(call: CallbackQuery, state: FSMContext, session):
    if not await guard(call, session):
        return
    deal_id = int(call.data.split(":")[2])
    await call.answer()
    await state.set_state(AdminStates.mod_note)
    await state.update_data(mod_note={"deal_id": deal_id, "action": "to_dispute"})
    await mod_deal_card(call, state, session, deal_id, note_prompt=True)


@router.callback_query(F.data.startswith("adm:note_skip:"))
async def adm_note_skip(call: CallbackQuery, state: FSMContext, session):
    if not await guard(call, session):
        return
    deal_id = int(call.data.split(":")[2])
    await apply_moderation(call, state, session, deal_id, "")


@router.message(AdminStates.mod_note, F.text)
async def adm_note_input(message: Message, state: FSMContext, session):
    if not await guard(message, session):
        return
    data = await state.get_data()
    ta = data.get("ticket_answer")
    if ta:
        from bot.db.models import SupportTicket
        from bot.admin.finance import notify_user
        t = await session.get(SupportTicket, ta["tid"])
        if t is not None:
            u = await usvc.get_user_by_id(session, t.user_id)
            t.answer = message.text[:3000]
            t.status = "ANSWERED"
            await session.commit()
            if u:
                await notify_user(message.bot, u.tg_id,
                                  f"🆘 <b>ОТВЕТ ПОДДЕРЖКИ (тикет #{t.id})</b>\n\n{LINE}\n\n"
                                  f"<b>{esc(message.text[:1500])}</b>")
            await catalog.log_admin_action(session, message.from_user.id, "ANSWER_TICKET",
                                           f"ticket:{t.id}", "OPEN", "ANSWERED")
        await state.clear()
        await message.answer("✅ <b>Ответ отправлен пользователю.</b>", parse_mode="HTML")
        return
    mn = data.get("mod_note", {})
    if not mn.get("deal_id"):
        await state.clear()
        await message.answer("ℹ️ <b>Состояние сброшено.</b>", parse_mode="HTML")
        return
    await apply_moderation(message, state, session, mn["deal_id"], message.text.strip(),
                           from_message=True)


async def apply_moderation(event, state, session, deal_id: int, note: str,
                           from_message: bool = False):
    data = await state.get_data()
    mn = data.get("mod_note", {"action": "reject"})
    action = mn.get("action", "reject")
    deal = await dsvc.get_deal(session, deal_id)
    if deal is None:
        await event.answer("Не найдено", show_alert=True)
        await state.clear()
        return
    seller = await usvc.get_user_by_id(session, deal.seller_id)
    buyer = await usvc.get_user_by_id(session, deal.buyer_id) if deal.buyer_id else None
    if action == "reject":
        ok = await dsvc.reject_deal(session, deal, note)
        if ok:
            await catalog.log_admin_action(session, event.from_user.id, "REJECT_DEAL",
                                           f"deal:{deal.code}", deal.status, "REJECTED")
            if buyer:
                try:
                    await event.bot.send_message(
                        buyer.tg_id,
                        f"🔄 <b>СДЕЛКА #{deal.code} ОТКЛОНЕНА</b>\n\n{LINE}\n\n"
                        f"💰 <b>Возврат: {fmt_money(deal.price)} ₽ на ваш баланс.</b>\n\n"
                        + (f"📝 <b>Комментарий: {esc(note[:300])}</b>" if note else ""),
                        parse_mode="HTML")
                except Exception:
                    pass
            if seller:
                try:
                    await event.bot.send_message(
                        seller.tg_id,
                        f"❌ <b>СДЕЛКА #{deal.code} ОТКЛОНЕНА МОДЕРАТОРОМ</b>\n\n{LINE}\n\n"
                        f"🔒 <b>Заморозка {fmt_money(deal.seller_amount)} ₽ снята.</b>\n\n"
                        + (f"📝 <b>Комментарий: {esc(note[:300])}</b>" if note else
                           "<b>Обратитесь в поддержку для уточнения.</b>"),
                        parse_mode="HTML")
                except Exception:
                    pass
    elif action == "to_dispute":
        await dsvc.dispute_deal(session, deal)
        from bot.db.models import Dispute as Dsp
        rec = Dsp(deal_id=deal.id, user_id=await event_from_uid(session, event),
                  reason="Решением модератора", description=note or "Переведено в спор",
                  status="OPEN")
        session.add(rec)
        await session.commit()
        await catalog.log_admin_action(session, event.from_user.id, "DEAL_TO_DISPUTE",
                                       f"deal:{deal.code}", deal.status, "DISPUTED")
    await state.clear()
    if from_message:
        await event.answer("✅ Решение применено", parse_mode="HTML")
        await mod_list_via_call(event, state, session)
    else:
        await event.answer("✅ Решение применено")
        await mod_list(event, state, session)


async def event_from_uid(session, event) -> int:
    """DB id администратора, инициировавшего действие (для записи спора)."""
    tg_id = getattr(event.from_user, "id", None)
    if tg_id is None:
        return 0
    u = await usvc.get_user(session, tg_id)
    if u is None:
        from bot.db.models import User as U
        u = U(tg_id=tg_id, registered=True, nickname="admin")
        session.add(u)
        await session.commit()
    return u.id


async def mod_list_via_call(event, state, session):
    """Обновление очереди модерации после текстового ввода админа."""
    if isinstance(event, CallbackQuery):
        await mod_list(event, state, session)
    else:
        # отправим карточку очереди новым сообщением
        queue = await dsvc.moderation_queue(session)
        rows = [[(f"⏳ #{d.code}", f"adm:deal:{d.id}")] for d in queue[:15]]
        rows.append([("🔙 В АДМИНКУ", "ad:home")])
        lines = [f"<b>⏳ #{d.code} — {esc(d.title[:40])} — {fmt_money(d.price)} ₽</b>"
                 for d in queue[:15]]
        await event.answer(f"🛡 <b>МОДЕРАЦИЯ ({len(queue)})</b>\n\n" +
                           ("\n".join(lines) or "<b>Очередь пуста ✅</b>"),
                           reply_markup=inl(rows), parse_mode="HTML")
