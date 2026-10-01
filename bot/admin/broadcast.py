"""Админ: рассылка (текст + баннер + inline-кнопки + выбор аудитории) с предпросмотром."""
from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, Message
from sqlalchemy import or_, select

from bot.admin.main import guard
from bot.db.models import User
from bot.handlers.deal_create import esc
from bot.keyboard.kbs import inl
from bot.services import catalog
from bot.states.states import AdminStates
from bot.utils.renderer import render_screen, show
from bot.utils.texts import LINE

log = logging.getLogger("playerok.bcast")
router = Router(name="admin_bcast")


@router.callback_query(F.data == "ad:bcast")
async def ad_bcast(call: CallbackQuery, state: FSMContext, session):
    if not await guard(call, session):
        return
    await call.answer()
    await state.set_state(AdminStates.bc_text)
    await state.update_data(bc={})
    await show(call, session,
               text=f"📣 <b>РАССЫЛКА</b>\n\n{LINE}\n\n"
                    "<b>Шаг 1. Отправьте текст рассылки.</b>\n\n"
                    "<b>Дальше бот спросит про баннер, кнопки и аудиторию.</b>",
               kb=inl([[("❌ ОТМЕНА", "ad:home")]]), banner="ADMIN", state=state)


@router.message(AdminStates.bc_text, F.text)
async def bc_text(message: Message, state: FSMContext, session):
    if not await guard(message, session):
        return
    data = await state.get_data()
    bc = data.get("bc", {})
    bc["text"] = message.text[:3000]
    await state.update_data(bc=bc)
    await state.set_state(AdminStates.bc_banner)
    await render_screen(message.bot, session, chat_id=message.chat.id,
                        text=f"📣 <b>РАССЫЛКА · Шаг 2</b>\n\n{LINE}\n\n"
                             "<b>Отправьте баннер (картинку) для рассылки</b>\n\n"
                             "<b>Или отправьте «нет», чтобы без картинки:</b>",
                        kb=inl([[("❌ ОТМЕНА", "ad:home")]]), state=state)


@router.message(AdminStates.bc_banner, F.photo)
async def bc_banner_photo(message: Message, state: FSMContext, session):
    if not await guard(message, session):
        return
    data = await state.get_data()
    bc = data.get("bc", {})
    bc["banner_file_id"] = message.photo[-1].file_id
    await state.update_data(bc=bc)
    await _ask_btn1(message, state)


@router.message(AdminStates.bc_banner, F.text)
async def bc_banner_skip(message: Message, state: FSMContext, session):
    if not await guard(message, session):
        return
    if message.text.strip().lower() not in {"нет", "no", "-"}:
        await message.answer("⚠️ <b>Отправьте картинку или слово «нет».</b>", parse_mode="HTML")
        return
    data = await state.get_data()
    bc = data.get("bc", {})
    bc["banner_file_id"] = None
    await state.update_data(bc=bc)
    await _ask_btn1(message, state)


async def _ask_btn1(event, state):
    await state.set_state(AdminStates.bc_btn1_text)
    await event.answer(f"🔗 <b>Шаг 3. Кнопка 1</b>\n\n{LINE}\n\n"
                       "<b>Отправьте текст кнопки и ссылку через пробел:</b>\n"
                       "<b>Например: 📢 НАШ КАНАЛ https://t.me/Playerok</b>\n\n"
                       "<b>Или «нет» — перейти к выбору аудитории.</b>",
                       parse_mode="HTML")


@router.message(AdminStates.bc_btn1_text, F.text)
async def bc_btn1(message: Message, state: FSMContext, session):
    if not await guard(message, session):
        return
    data = await state.get_data()
    bc = data.get("bc", {})
    txt = message.text.strip()
    if txt.lower() in {"нет", "no", "-"}:
        bc["btn1"] = None
        await state.update_data(bc=bc)
        await _ask_audience(message, state)
        return
    parts = txt.split(maxsplit=1)
    if len(parts) != 2 or not parts[1].startswith(("http", "t.me", "@")):
        await message.answer("⚠️ <b>Формат: ТЕКСТ https://ссылка</b>", parse_mode="HTML")
        return
    bc["btn1"] = {"text": parts[0][:40], "url": parts[1][:300]}
    await state.update_data(bc=bc)
    await state.set_state(AdminStates.bc_btn2_text)
    await message.answer(f"✅ <b>Кнопка 1 сохранена.</b>\n\n"
                         f"<b>Шаг 4. Кнопка 2 (или «нет»):</b>", parse_mode="HTML")


@router.message(AdminStates.bc_btn2_text, F.text)
async def bc_btn2(message: Message, state: FSMContext, session):
    if not await guard(message, session):
        return
    data = await state.get_data()
    bc = data.get("bc", {})
    txt = message.text.strip()
    if txt.lower() in {"нет", "no", "-"}:
        bc["btn2"] = None
    else:
        parts = txt.split(maxsplit=1)
        if len(parts) != 2 or not parts[1].startswith(("http", "t.me", "@")):
            await message.answer("⚠️ <b>Формат: ТЕКСТ https://ссылка</b>", parse_mode="HTML")
            return
        bc["btn2"] = {"text": parts[0][:40], "url": parts[1][:300]}
    await state.update_data(bc=bc)
    await _ask_audience(message, state)


AUDIENCES = {
    "all": ("👥 ВСЕ ПОЛЬЗОВАТЕЛИ", lambda: select(User.tg_id).where(User.registered.is_(True))),
    "active": ("🟢 С АКТИВНЫМИ СДЕЛКАМИ", None),
    "new": ("🆕 НОВЫЕ (без сделок)",
            lambda: select(User.tg_id).where(User.registered.is_(True),
                                             User.deals_created == 0)),
}


async def _ask_audience(event, state):
    await state.set_state(AdminStates.bc_audience)
    kb = inl([
        [("👥 ВСЕ", "adb:aud:all")],
        [("🟢 С АКТИВНЫМИ СДЕЛКАМИ", "adb:aud:active")],
        [("🆕 БЕЗ СДЕЛОК", "adb:aud:new")],
    ])
    if isinstance(event, Message):
        await event.answer(f"🎯 <b>Шаг 5. Выберите аудиторию рассылки:</b>",
                           reply_markup=kb, parse_mode="HTML")
    else:
        await show(event, None, text=f"🎯 <b>Шаг 5. Выберите аудиторию:</b>", kb=kb,
                   state=state)


@router.callback_query(AdminStates.bc_audience, F.data.startswith("adb:aud:"))
async def adb_aud(call: CallbackQuery, state: FSMContext, session):
    if not await guard(call, session):
        return
    aud = call.data.split(":")[2]
    if aud not in AUDIENCES:
        await call.answer("Неверная аудитория", show_alert=True)
        return
    data = await state.get_data()
    bc = data.get("bc", {})
    bc["audience"] = aud
    # собрать список tg_id
    if aud == "active":
        from bot.db.models import Deal
        rows = (await session.execute(
            select(User.tg_id).join(Deal, or_(Deal.seller_id == User.id,
                                              Deal.buyer_id == User.id))
            .where(Deal.status.notin_(["COMPLETED", "CANCELLED", "REJECTED", "REFUNDED"]),
                   User.registered.is_(True)).distinct())).scalars().all()
    else:
        stmt = AUDIENCES[aud][0]()
        rows = (await session.execute(stmt)).scalars().all()
    bc["targets"] = list(rows)
    await state.update_data(bc=bc)
    await state.set_state(AdminStates.bc_preview)
    # предпросмотр
    kb_rows = []
    if bc.get("btn1"):
        kb_rows.append([InlineKeyboardButton(text=bc["btn1"]["text"], url=bc["btn1"]["url"])])
    if bc.get("btn2"):
        kb_rows.append([InlineKeyboardButton(text=bc["btn2"]["text"], url=bc["btn2"]["url"])])
    kb_rows.append([InlineKeyboardButton(text="🏠 PLAYEROK", url="https://t.me/" + "Playerok")])
    from aiogram.types import InlineKeyboardMarkup
    preview_kb = InlineKeyboardMarkup(inline_keyboard=kb_rows)
    n = len(bc["targets"])
    intro = (f"👀 <b>ПРЕДПРОСМОТР РАССЫЛКИ</b>\n\n{LINE}\n\n"
             f"<b>Аудитория: {AUDIENCES[aud][0]}</b>\n"
             f"<b>Получателей: {n}</b>\n\n<b>Ниже — как увидят пользователи:</b>")
    try:
        if bc.get("banner_file_id"):
            await call.bot.send_photo(call.from_user.id, photo=bc["banner_file_id"],
                                      caption=bc["text"], reply_markup=preview_kb,
                                      parse_mode="HTML")
        else:
            await call.bot.send_message(call.from_user.id, text=bc["text"],
                                        reply_markup=preview_kb, parse_mode="HTML")
    except TelegramAPIError:
        log.warning("preview send failed")
    confirm = inl([
        [("🚀 ОТПРАВИТЬ РАССЫЛКУ", "adb:send"), ("✏️ ЗАНОВО", "adb:restart")],
        [("🔙 В АДМИНКУ", "ad:home")],
    ])
    await show(call, session, text=intro, kb=confirm, banner="ADMIN", state=state)


@router.callback_query(F.data == "adb:restart")
async def adb_restart(call: CallbackQuery, state: FSMContext, session):
    if not await guard(call, session):
        return
    await ad_bcast(call, state, session)


@router.callback_query(AdminStates.bc_preview, F.data == "adb:send")
async def adb_send(call: CallbackQuery, state: FSMContext, session):
    if not await guard(call, session):
        return
    data = await state.get_data()
    bc = data.get("bc", {})
    targets = bc.get("targets", [])
    if not targets or not bc.get("text"):
        await call.answer("Нет данных рассылки", show_alert=True)
        return
    await call.answer(f"🚀 Запуск рассылки на {len(targets)} получателей…")
    await state.clear()
    await catalog.log_admin_action(session, call.from_user.id, "BROADCAST_START",
                                   "broadcast", None, f"{len(targets)} targets")
    await show(call, session,
                           text=f"🚀 <b>РАССЫЛКА ЗАПУЩЕНА</b>\n\n{LINE}\n\n"
                                f"<b>Получателей: {len(targets)}</b>\n\n"
                                "<b>Отправка идёт в фоне — не закрывайте панель. ⏳</b>",
                           kb=inl([[("🔙 В АДМИНКУ", "ad:home")]]), state=state)
    # фоновая отправка
    asyncio.create_task(_do_broadcast(call.bot, bc, len(targets)))


async def _do_broadcast(bot: Bot, bc: dict, total: int):
    kb_rows = []
    if bc.get("btn1"):
        kb_rows.append([InlineKeyboardButton(text=bc["btn1"]["text"], url=bc["btn1"]["url"])])
    if bc.get("btn2"):
        kb_rows.append([InlineKeyboardButton(text=bc["btn2"]["text"], url=bc["btn2"]["url"])])
    from aiogram.types import InlineKeyboardMarkup
    kb = InlineKeyboardMarkup(inline_keyboard=kb_rows) if kb_rows else None
    ok = fail = 0
    for i, tg_id in enumerate(bc.get("targets", [])):
        try:
            if bc.get("banner_file_id"):
                await bot.send_photo(tg_id, photo=bc["banner_file_id"],
                                     caption=bc["text"], reply_markup=kb, parse_mode="HTML")
            else:
                await bot.send_message(tg_id, text=bc["text"], reply_markup=kb,
                                       parse_mode="HTML")
            ok += 1
        except TelegramAPIError:
            fail += 1
        except Exception:
            fail += 1
        await asyncio.sleep(0.035)  # лимит ~30 msg/s
        if (i + 1) % 200 == 0:
            log.info("broadcast progress: %s/%s", i + 1, total)
    from bot.config.settings import get_config
    owner = get_config().OWNER_ID
    try:
        await bot.send_message(
            owner, f"✅ <b>РАССЫЛКА ЗАВЕРШЕНА</b>\n\n{LINE}\n\n"
                   f"<b>Доставлено: {ok}</b>\n<b>Ошибок: {fail}</b>", parse_mode="HTML")
    except Exception:
        log.exception("cannot report broadcast result")
