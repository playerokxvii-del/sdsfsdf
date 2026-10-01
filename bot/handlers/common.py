"""Главное меню, навигация, FAQ, новости, поддержка, профиль."""
from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.base import get_commission_percent, get_setting
from bot.keyboard.kbs import (
    faq_kb, faq_topic_kb, inl, main_menu_kb, news_kb, profile_kb, public_profile_kb,
    settings_kb, support_kb,
)
from bot.services import catalog, users as usvc
from bot.states.states import SettingsStates, SupportStates
from bot.utils.renderer import render_screen, show
from bot.utils.texts import (
    CHANNEL_USERNAME, FAQ_ACCOUNT, FAQ_COMMISSION, FAQ_DEALS, FAQ_DISPUTES, FAQ_MAIN,
    FAQ_PAYMENTS, FAQ_SECURITY, FAQ_SUPPORT_T, MENU_TEXT, SUPPORT_TEXT, FAQ_WITHDRAW,
    LINE, SUPPORT_USERNAME, err_text, fmt_money, rating_text,
)

log = logging.getLogger("playerok.common")
router = Router(name="common")


# ---------- Меню ----------

async def menu_screen(event, session: AsyncSession, state: FSMContext):
    tg_id = event.from_user.id
    user = await usvc.get_user(session, tg_id)
    if user is None or not user.registered:
        # не зарегистрирован — перенаправляем
        from bot.handlers.registration import cmd_start
        if isinstance(event, CallbackQuery):
            await event.message.edit_text(
                "👤 <b>Сначала пройдите регистрацию.</b>\n\n<b>Отправьте /start.</b>",
                reply_markup=None, parse_mode="HTML")
            await event.answer()
        return
    text = MENU_TEXT.format(nickname=user.nickname or "друг",
                            balance=fmt_money(user.available_balance),
                            frozen=fmt_money(user.frozen_balance))
    await show(event, session, text=text, kb=main_menu_kb(), banner="MENU", state=state)
    if isinstance(event, CallbackQuery):
        await event.answer()


@router.callback_query(F.data == "nav:menu")
async def nav_menu(call: CallbackQuery, state: FSMContext, session):
    await state.clear()
    await menu_screen(call, session, state)


@router.message(Command("menu"))
async def cmd_menu(message: Message, state: FSMContext, session):
    await state.clear()
    await menu_screen(message, session, state)


@router.callback_query(F.data == "back")
async def nav_back(call: CallbackQuery, state: FSMContext, session):
    """Универсальный «назад»: из подэкрана FAQ — в FAQ; иначе — в меню."""
    data = await state.get_data()
    prev = data.get("_last_section")
    await call.answer()
    if prev == "faq":
        await show(call, session, text=FAQ_MAIN, kb=faq_kb(), banner="FAQ", state=state)
    elif prev == "wallet":
        from bot.handlers.wallet import wallet_screen
        await wallet_screen(call, session, state)
    elif prev == "deals":
        from bot.handlers.deals_list import deals_menu_screen
        await deals_menu_screen(call, session, state)
    else:
        await menu_screen(call, session, state)


# ---------- Профиль ----------

async def profile_screen(event, session: AsyncSession, state: FSMContext):
    user = await usvc.get_user(session, event.from_user.id)
    if user is None or not user.registered:
        await show(event, session, text="👤 <b>Сначала пройдите регистрацию:</b> /start",
                   kb=inl([("🏠 В МЕНЮ", "nav:menu")]), state=state)
        if isinstance(event, CallbackQuery):
            await event.answer()
        return
    marked = 0
    text = f"""👤 <b>МОЙ ПРОФИЛЬ</b>

{LINE}

👤 <b>Никнейм: {user.nickname}</b>
🆔 <b>ID: {user.id}</b>
⭐ <b>Рейтинг: {rating_text(user)}</b>
🤝 <b>Всего сделок: {user.deals_created}</b>
✅ <b>Успешных: {user.deals_completed}</b>
📌 <b>Отмеченных: {marked}</b>
💰 <b>Баланс: {fmt_money(user.available_balance)} ₽</b>
🔒 <b>Заморожено: {fmt_money(user.frozen_balance)} ₽</b>"""
    await show(event, session, text=text, kb=profile_kb(), banner="PROFILE", state=state)
    if isinstance(event, CallbackQuery):
        await event.answer()


@router.callback_query(F.data == "profile:me")
async def profile_me(call: CallbackQuery, state: FSMContext, session):
    await profile_screen(call, session, state)


@router.callback_query(F.data == "profile:stats")
async def profile_stats(call: CallbackQuery, state: FSMContext, session):
    user = await usvc.get_user(session, call.from_user.id)
    await call.answer()
    if user is None:
        await menu_screen(call, session, state)
        return
    text = f"""📊 <b>СТАТИСТИКА</b>

{LINE}

👤 <b>Никнейм: {user.nickname}</b>
🆔 <b>ID: {user.id}</b>
🤝 <b>Создано сделок: {user.deals_created}</b>
✅ <b>Успешно завершено: {user.deals_completed}</b>
⭐ <b>Рейтинг: {rating_text(user)}</b>
💰 <b>Доступно: {fmt_money(user.available_balance)} ₽</b>
🔒 <b>Заморожено: {fmt_money(user.frozen_balance)} ₽</b>
📅 <b>На Playerok с: {user.created_at.strftime('%d.%m.%Y')}</b>"""
    await show(call, session, text=text,
               kb=inl([[("🔙 НАЗАД", "profile:me")], ("🏠 В МЕНЮ", "nav:menu")]),
               banner="PROFILE", state=state)


@router.callback_query(F.data == "profile:settings")
async def profile_settings(call: CallbackQuery, state: FSMContext, session):
    await call.answer()
    await state.set_state(SettingsStates.change_nick)
    await show(call, session,
               text=f"⚙️ <b>НАСТРОЙКИ</b>\n\n{LINE}\n\n"
                    "<b>✏️ Чтобы изменить никнейм, просто отправьте новый ник сообщением "
                    "(3–20 символов: A–Z, 0–9, _).</b>\n\n"
                    "<b>Или вернитесь назад.</b>",
               kb=settings_kb(), state=state)


@router.message(SettingsStates.change_nick, F.text)
async def change_nick(message: Message, state: FSMContext, session):
    nick = message.text.strip().lstrip("@")
    if nick.lower() in {"отмена", "cancel"}:
        await state.clear()
        await profile_screen(message, session, state)
        return
    user = await usvc.get_user(session, message.from_user.id)
    ok = user and await usvc.set_nickname(session, user, nick)
    if ok:
        await state.clear()
        await message.answer("✅ <b>Никнейм обновлён!</b>", parse_mode="HTML")
        await profile_screen(message, session, state)
    else:
        await message.answer(
            "❌ <b>Никнейм недоступен</b> (занят или некорректен).\n\n"
            "<b>Попробуйте другой или «отмена».</b>", parse_mode="HTML")


# ---------- Публичный профиль продавца ----------

@router.callback_query(F.data.startswith("user:view:"))
async def user_view(call: CallbackQuery, session, state: FSMContext):
    deal_id = int(call.data.split(":")[2])
    from bot.services import deals as dsvc
    deal = await dsvc.get_deal(session, deal_id)
    await call.answer()
    if deal is None:
        await show(call, session, text=err_text(), kb=inl([("🏠 В МЕНЮ", "nav:menu")]),
                   state=state)
        return
    seller = await usvc.get_user_by_id(session, deal.seller_id)
    text = f"""👤 <b>ПРОФИЛЬ ПОЛЬЗОВАТЕЛЯ</b>

{LINE}

👤 <b>Никнейм: {seller.nickname}</b>
⭐ <b>Рейтинг: {rating_text(seller)}</b>
🤝 <b>Сделок создано: {seller.deals_created}</b>
✅ <b>Успешных: {seller.deals_completed}</b>"""
    await show(call, session, text=text, kb=public_profile_kb(seller.id), state=state)


@router.callback_query(F.data.startswith("rev:user:"))
async def reviews_of_user(call: CallbackQuery, session, state: FSMContext):
    uid = int(call.data.split(":")[2])
    reviews = await catalog.user_reviews(session, uid)
    await call.answer()
    if not reviews:
        text = f"⭐ <b>ОТЗЫВОВ ПОКА НЕТ</b>\n\n{LINE}\n\n<b>Станьте первым!</b>"
    else:
        lines = []
        for r in reviews[:10]:
            author = await usvc.get_user_by_id(session, r.author_id)
            stars = "⭐" * r.stars
            body = f"\n<i>{_esc(r.text)}</i>" if r.text else ""
            lines.append(f"<b>{stars}</b>\n<b>{author.nickname if author else 'Аноним'}:</b>{body}")
        text = f"⭐ <b>ОТЗЫВЫ</b>\n\n{LINE}\n\n" + "\n\n".join(lines)
    await show(call, session, text=text,
               kb=inl([[("🔙 НАЗАД", "profile:me")], ("🏠 В МЕНЮ", "nav:menu")]),
               state=state)


def _esc(s: str | None) -> str:
    if not s:
        return ""
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# ---------- Отзывы пользователя ----------

@router.callback_query(F.data == "rev:mine")
async def my_reviews(call: CallbackQuery, session, state: FSMContext):
    user = await usvc.get_user(session, call.from_user.id)
    await call.answer()
    if user is None:
        await menu_screen(call, session, state)
        return
    reviews = await catalog.user_reviews(session, user.id)
    if not reviews:
        text = f"⭐ <b>У ВАС ПОКА НЕТ ОТЗЫВОВ</b>\n\n{LINE}\n\n<b>Завершайте сделки — покупатели смогут вас оценить.</b>"
    else:
        parts = []
        for r in reviews[:10]:
            author = await usvc.get_user_by_id(session, r.author_id)
            body = f"\n<i>{_esc(r.text)}</i>" if r.text else ""
            parts.append(f"<b>{'⭐' * r.stars} — {author.nickname if author else 'Аноним'}:</b>{body}")
        text = f"⭐ <b>МОИ ОТЗЫВЫ</b>\n\n{LINE}\n\n" + "\n\n".join(parts)
    await show(call, session, text=text,
               kb=inl([[("🔙 ПРОФИЛЬ", "profile:me")], ("🏠 В МЕНЮ", "nav:menu")]),
               banner="PROFILE", state=state)


# ---------- FAQ ----------

FAQ_TOPICS = {
    "commission": lambda c: FAQ_COMMISSION.format(commission=c),
    "deals": lambda c: FAQ_DEALS,
    "security": lambda c: FAQ_SECURITY.format(channel=CHANNEL_USERNAME, support=SUPPORT_USERNAME),
    "payments": lambda c: FAQ_PAYMENTS,
    "withdraw": lambda c: FAQ_WITHDRAW,
    "disputes": lambda c: FAQ_DISPUTES,
    "account": lambda c: FAQ_ACCOUNT,
}


@router.callback_query(F.data == "faq:main")
async def faq_main(call: CallbackQuery, state: FSMContext, session):
    await state.update_data(_last_section="faq")
    await call.answer()
    await show(call, session, text=FAQ_MAIN, kb=faq_kb(), banner="FAQ", state=state)


@router.callback_query(F.data.startswith("faq:"))
async def faq_topic(call: CallbackQuery, state: FSMContext, session):
    key = call.data.split(":", 1)[1]
    if key == "main":
        await faq_main(call, state, session)
        return
    if key not in FAQ_TOPICS:
        await call.answer()
        await faq_main(call, state, session)
        return
    commission = await get_commission_percent(session)
    text = FAQ_TOPICS[key](fmt_money(commission))
    await call.answer()
    await state.update_data(_last_section="faq")
    await show(call, session, text=text, kb=faq_topic_kb("faq:main"), banner="FAQ", state=state)


# ---------- Новости ----------

@router.callback_query(F.data == "news:list")
async def news_list(call: CallbackQuery, state: FSMContext, session):
    posts = await catalog.published_news(session)
    await call.answer()
    channel_url = await get_setting(session, "channel_link", "https://t.me/Playerok")
    if not posts:
        text = f"📢 <b>НОВОСТЕЙ ПОКА НЕТ</b>\n\n{LINE}\n\n<b>Следите за обновлениями на канале:</b>\n📢 <b>{CHANNEL_USERNAME}</b>"
        from aiogram.types import InlineKeyboardButton
        kb = inl([[("🏠 В МЕНЮ", "nav:menu")]])
        kb.inline_keyboard.insert(0, [InlineKeyboardButton(text="📢 ОТКРЫТЬ КАНАЛ", url=channel_url)])
        await show(call, session, text=text, kb=kb, banner="NEWS", state=state)
        return
    text = f"📢 <b>НОВОСТИ PLAYEROK</b>\n\n{LINE}\n\n<b>Выберите новость 👇</b>"
    await show(call, session, text=text, kb=news_kb(list(posts)), banner="NEWS", state=state)


@router.callback_query(F.data.startswith("news:view:"))
async def news_view(call: CallbackQuery, state: FSMContext, session):
    pid = int(call.data.split(":")[2])
    from bot.db.models import NewsPost
    post = await session.get(NewsPost, pid)
    await call.answer()
    if post is None:
        await news_list(call, state, session)
        return
    text = f"📢 <b>{_esc(post.title)}</b>\n\n{LINE}\n\n<b>{_esc(post.text)}</b>\n\n📅 <b>{post.created_at.strftime('%d.%m.%Y')}</b>"
    await show(call, session, text=text,
               kb=inl([[("🔙 К НОВОСТЯМ", "news:list")], ("🏠 В МЕНЮ", "nav:menu")]),
               banner="NEWS", state=state)


# ---------- Поддержка ----------

@router.callback_query(F.data == "support:menu")
async def support_menu(call: CallbackQuery, state: FSMContext, session):
    url = await get_setting(session, "support_link", "https://t.me/PIayerokSupports")
    await call.answer()
    await state.set_state(SupportStates.message)
    text = SUPPORT_TEXT.format(support=SUPPORT_USERNAME)
    await show(call, session, text=text, kb=support_kb(url), banner="SUPPORT", state=state)


@router.callback_query(F.data == "support:write")
async def support_write(call: CallbackQuery, state: FSMContext, session):
    await call.answer()
    await state.set_state(SupportStates.message)
    await show(call, session,
               text=f"💬 <b>ОТПРАВЬТЕ СООБЩЕНИЕ ПОДДЕРЖКЕ</b>\n\n{LINE}\n\n"
                    "<b>Опишите проблему одним сообщением — оно будет передано ответственному сотруднику.</b>",
               kb=inl([[("🔙 НАЗАД", "support:menu")], ("🏠 В МЕНЮ", "nav:menu")]),
               banner="SUPPORT", state=state)


@router.message(SupportStates.message, F.text)
async def support_message(message: Message, state: FSMContext, session):
    from bot.db.models import SupportTicket
    user = await usvc.ensure_user(session, message.from_user.id,
                                  message.from_user.username, message.from_user.first_name)
    t = SupportTicket(user_id=user.id, message=message.text[:3000])
    session.add(t)
    await session.commit()
    # уведомление админам
    from bot.config.settings import get_config
    admins = [get_config().OWNER_ID]
    st = await get_setting(session, "admin_ids", "")
    admins += [int(x) for x in st.replace(" ", "").split(",") if x.isdigit()]
    for a in dict.fromkeys(admins):
        try:
            await message.bot.send_message(
                a, f"🆘 <b>НОВОЕ ОБРАЩЕНИЕ В ПОДДЕРЖКУ</b>\n\n"
                  f"👤 <b>{user.nickname or user.username or user.tg_id}</b> (tg {message.from_user.id})\n\n"
                  f"<b>{_esc(message.text[:2000])}</b>\n\n"
                  f"🎫 Тикет #{t.id}", parse_mode="HTML")
        except Exception:
            log.warning("cannot notify admin %s about ticket", a)
    await state.clear()
    await message.answer(
        f"✅ <b>СООБЩЕНИЕ ОТПРАВЛЕНО</b>\n\n{LINE}\n\n"
        "<b>Ответственный сотрудник получит ваше обращение и ответит. 🆘</b>",
        parse_mode="HTML")
