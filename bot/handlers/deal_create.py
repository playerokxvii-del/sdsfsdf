"""Создание сделки: категории → игра → товар → ссылка → цена → предпросмотр."""
from __future__ import annotations

import logging
import re
from decimal import Decimal, InvalidOperation

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.base import get_commission_percent
from bot.keyboard.kbs import categories_kb, games_kb, inl, preview_kb
from bot.services import catalog, deals as dsvc, users as usvc
from bot.states.states import DealCreate
from bot.utils.renderer import render_screen, show
from bot.utils.texts import (
    CREATE_DEAL_STEPS, LINE, PREVIEW_TMPL, err_text, fmt_money,
)

log = logging.getLogger("playerok.dealcreate")
router = Router(name="deal_create")

URL_RE = re.compile(r"^https?://\S{2,300}$", re.I)


def _parse_price(text: str) -> Decimal | None:
    t = text.replace(",", ".").replace("₽", "").replace("руб", "").strip()
    try:
        p = Decimal(t)
    except (InvalidOperation, ValueError):
        return None
    if p <= 0 or p > Decimal("100000000"):
        return None
    return p


@router.callback_query(F.data == "deal:new")
async def deal_new(call: CallbackQuery, state: FSMContext, session):
    user = await usvc.get_user(session, call.from_user.id)
    if user is None or not user.registered:
        await call.answer("Сначала пройдите регистрацию: /start", show_alert=True)
        return
    cats = await catalog.active_categories(session)
    if not cats:
        await call.answer()
        await show(call, session, text="📂 <b>КАТЕГОРИИ ВРЕМЕННО НЕДОСТУПНЫ</b>\n\n"
                   "<b>Попробуйте позже или обратитесь в поддержку.</b>",
                   kb=inl([("🏠 В МЕНЮ", "nav:menu")]), banner="CREATE_DEAL", state=state)
        return
    await state.set_state(DealCreate.category)
    await state.update_data(dc={})
    await call.answer()
    await show(call, session, text=CREATE_DEAL_STEPS.format(step="1", body="""
📂 <b>Выберите категорию товара:</b>"""),
               kb=categories_kb(list(cats)), banner="CREATE_DEAL", state=state)


@router.callback_query(DealCreate.category, F.data.startswith("dc:cat:"))
async def dc_cat(call: CallbackQuery, state: FSMContext, session):
    cat_id = int(call.data.split(":")[2])
    from bot.db.models import Category
    cat = await session.get(Category, cat_id)
    if cat is None or not cat.active:
        await call.answer("Категория недоступна", show_alert=True)
        return
    data = await state.get_data()
    dc = data.get("dc", {})
    dc["category_id"] = cat.id
    dc["category_name"] = f"{cat.emoji} {cat.name}"
    await state.update_data(dc=dc)
    await call.answer()
    if cat.requires_game:
        games = await catalog.category_games(session, cat.id)
        await state.set_state(DealCreate.game)
        await show(call, session,
                   text=CREATE_DEAL_STEPS.format(step="2", body=f"""
🎮 <b>Выберите игру / сервис для категории «{cat.name}»:</b>

<b>Или укажите свою игру кнопкой внизу.</b>"""),
                   kb=games_kb(list(games), cat.id), banner="CREATE_DEAL", state=state)
    else:
        await ask_title(call, state, session, dc)


@router.callback_query(DealCreate.game, F.data.startswith("dc:game:"))
async def dc_game(call: CallbackQuery, state: FSMContext, session):
    parts = call.data.split(":")
    cat_id, gid = int(parts[2]), parts[3]
    data = await state.get_data()
    dc = data.get("dc", {})
    if gid == "custom":
        await state.set_state(DealCreate.game_custom)
        await call.answer()
        await show(call, session,
                   text=CREATE_DEAL_STEPS.format(step="2", body="""
🎮 <b>Введите название вашей игры или сервиса</b> (до 60 символов):"""),
                   kb=inl([[("🔙 К СПИСКУ ИГР", f"dc:backgames:{cat_id}")],
                           ("🏠 В МЕНЮ", "nav:menu")]),
                   banner="CREATE_DEAL", state=state)
        return
    from bot.db.models import Game
    game = await session.get(Game, int(gid))
    if game is None:
        await call.answer("Игра недоступна", show_alert=True)
        return
    dc["game_id"] = game.id
    dc["game_name"] = game.name
    await state.update_data(dc=dc)
    await call.answer()
    await ask_title(call, state, session, dc)


@router.callback_query(DealCreate.game, F.data.startswith("dc:backgames:"))
async def dc_backgames(call: CallbackQuery, state: FSMContext, session):
    cat_id = int(call.data.split(":")[2])
    games = await catalog.category_games(session, cat_id)
    await call.answer()
    await show(call, session,
               text=CREATE_DEAL_STEPS.format(step="2", body="\n🎮 <b>Выберите игру / сервис:</b>"),
               kb=games_kb(list(games), cat_id), banner="CREATE_DEAL", state=state)


@router.message(DealCreate.game_custom, F.text)
async def dc_game_custom(message: Message, state: FSMContext, session):
    name = message.text.strip()[:60]
    if len(name) < 2:
        await message.answer("❌ <b>Слишком короткое название. Попробуйте ещё раз.</b>",
                             parse_mode="HTML")
        return
    data = await state.get_data()
    dc = data.get("dc", {})
    dc["game_id"] = None
    dc["game_name"] = name
    # добавим игру в каталог — пригодится другим пользователям
    if dc.get("category_id"):
        existing = await catalog.add_game(session, dc["category_id"], name)
    await state.update_data(dc=dc)
    await state.set_state(DealCreate.title)
    await render_screen(message.bot, session, chat_id=message.chat.id,
                        text=CREATE_DEAL_STEPS.format(step="3", body="""
📌 <b>Опишите товар подробно:</b>

<b>Что именно получает покупатель? (состав, условия, гарантии)</b>"""),
                        kb=inl([DEAL_CANCEL_ROW(), ("🏠 В МЕНЮ", "nav:menu")]),
                        banner_section="CREATE_DEAL", state=state)


def DEAL_CANCEL_ROW():
    return ("❌ ОТМЕНА", "dc:cancel")


async def ask_title(event, state: FSMContext, session, dc):
    await state.set_state(DealCreate.title)
    await show(event, session,
               text=CREATE_DEAL_STEPS.format(step="3", body="""
📌 <b>Опишите товар подробно:</b>

<b>Что именно получает покупатель? (состав, условия, гарантии)</b>"""),
               kb=inl([DEAL_CANCEL_ROW(), ("🏠 В МЕНЮ", "nav:menu")]),
               banner="CREATE_DEAL", state=state)
    await event.answer()


@router.message(DealCreate.title, F.text)
async def dc_title(message: Message, state: FSMContext, session):
    title = message.text.strip()
    if not (3 <= len(title) <= 500):
        await message.answer("❌ <b>Название должно быть от 3 до 500 символов.</b>",
                             parse_mode="HTML")
        return
    data = await state.get_data()
    dc = data.get("dc", {})
    dc["title"] = title
    await state.update_data(dc=dc)
    await state.set_state(DealCreate.link)
    await render_screen(message.bot, session, chat_id=message.chat.id,
                        text=CREATE_DEAL_STEPS.format(step="4", body="""
🔗 <b>Отправьте ссылку на товар</b> (например, на профиль, инвентарь, лот):

<b>Если ссылки нет — отправьте слово «нет».</b>"""),
                        kb=inl([DEAL_CANCEL_ROW(), ("🏠 В МЕНЮ", "nav:menu")]),
                        banner_section="CREATE_DEAL", state=state)


@router.message(DealCreate.link, F.text)
async def dc_link(message: Message, state: FSMContext, session):
    txt = message.text.strip()
    if txt.lower() in {"нет", "no", "-"}:
        link = ""
    elif URL_RE.match(txt):
        link = txt
    else:
        await message.answer(
            "❌ <b>Некорректная ссылка.</b>\n\n"
            "<b>Отправьте ссылку, начинающуюся с http:// или https://, либо слово «нет».</b>",
            parse_mode="HTML")
        return
    data = await state.get_data()
    dc = data.get("dc", {})
    dc["link"] = link
    await state.update_data(dc=dc)
    await state.set_state(DealCreate.price)
    await render_screen(message.bot, session, chat_id=message.chat.id,
                        text=CREATE_DEAL_STEPS.format(step="5", body="""
💰 <b>Укажите цену сделки в рублях</b> (число, например 1500):"""),
                        kb=inl([DEAL_CANCEL_ROW(), ("🏠 В МЕНЮ", "nav:menu")]),
                        banner_section="CREATE_DEAL", state=state)


@router.message(DealCreate.price, F.text)
async def dc_price(message: Message, state: FSMContext, session):
    price = _parse_price(message.text)
    if price is None:
        await message.answer("❌ <b>Введите корректную цену числом больше 0.</b>",
                             parse_mode="HTML")
        return
    data = await state.get_data()
    dc = data.get("dc", {})
    dc["price"] = str(price)
    await state.update_data(dc=dc)
    await show_preview(message, state, session, dc, from_message=True)


async def show_preview(event, state: FSMContext, session, dc, from_message: bool = False):
    pct = await get_commission_percent(session)
    price = Decimal(dc["price"])
    net = (price - price * pct / Decimal(100)).quantize(Decimal("0.01"))
    text = PREVIEW_TMPL.format(
        category=dc.get("category_name", "—"),
        game=dc.get("game_name", "—"),
        title=esc(dc.get("title", "—")),
        link=esc(dc.get("link") or "—"),
        price=fmt_money(price), commission=fmt_money(pct), net=fmt_money(net))
    await state.set_state(DealCreate.preview)
    if from_message and isinstance(event, Message):
        await render_screen(event.bot, session, chat_id=event.chat.id, text=text,
                            kb=preview_kb(), banner_section="CREATE_DEAL",
                            message_id=None, state=state)
    else:
        await show(event, session, text=text, kb=preview_kb(),
                   banner="CREATE_DEAL", state=state)


def esc(s: str) -> str:
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


@router.callback_query(DealCreate.preview, F.data == "dc:edit")
async def dc_edit(call: CallbackQuery, state: FSMContext, session):
    await call.answer()
    await state.set_state(DealCreate.category)
    cats = await catalog.active_categories(session)
    await show(call, session,
               text=CREATE_DEAL_STEPS.format(step="1", body="""
✏️ <b>Изменение: выберите категорию заново</b> (данные будут перезаписаны):"""),
               kb=categories_kb(list(cats)), banner="CREATE_DEAL", state=state)


@router.callback_query(DealCreate.preview, F.data == "dc:create")
async def dc_create(call: CallbackQuery, state: FSMContext, session):
    data = await state.get_data()
    dc = data.get("dc", {})
    user = await usvc.get_user(session, call.from_user.id)
    if user is None or not user.registered:
        await call.answer("Сначала пройдите регистрацию", show_alert=True)
        return
    price = _parse_price(dc.get("price", ""))
    if price is None or "title" not in dc:
        await call.answer("Данные сделки потеряны — начните заново", show_alert=True)
        await state.clear()
        return
    deal = await dsvc.create_deal(
        session, seller=user, category_id=dc.get("category_id"),
        game_id=dc.get("game_id"), title=dc["title"], link=dc.get("link"),
        price=price)
    await state.clear()
    from bot.handlers.deals_list import deal_link_for
    link = deal_link_for(call.bot, deal.code)
    text = DEAL_CREATED_TEXT(deal, link)
    kb = inl([[("📋 МОИ СДЕЛКИ", "deals:menu")], ("🏠 В МЕНЮ", "nav:menu")])
    await call.answer()
    await show(call, session, text=text, kb=kb, banner="CREATE_DEAL", state=state)


def DEAL_CREATED_TEXT(deal, link: str) -> str:
    from bot.utils.texts import DEAL_CREATED_TMPL
    return DEAL_CREATED_TMPL.format(code=deal.code, title=esc(deal.title),
                                    price=fmt_money(deal.price), deal_link=link)


@router.callback_query(F.data == "dc:cancel")
async def dc_cancel(call: CallbackQuery, state: FSMContext, session):
    await state.clear()
    await call.answer()
    from bot.handlers.common import menu_screen
    await menu_screen(call, session, state)
