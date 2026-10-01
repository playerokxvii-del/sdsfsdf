"""Регистрация: /start → контакт → никнейм → подписка → меню."""
from __future__ import annotations

import logging

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.config.settings import get_config
from bot.db.base import get_setting
from bot.keyboard.kbs import REG_KB, DONE_REG_BTN, inl, subscribe_kb
from bot.services import users as usvc
from bot.states.states import Registration
from bot.utils.renderer import render_screen, show
from bot.utils.texts import (
    MENU_TEXT, PHONE_OK_NICK_PROMPT, REG_DONE_TEXT, START_TEXT, SUBSCRIBE_TEXT,
    SUPPORT_USERNAME, err_text, fmt_money,
)

log = logging.getLogger("playerok.registration")
router = Router(name="registration")


async def bot_username(bot: Bot) -> str:
    cfg = get_config()
    if cfg.BOT_USERNAME:
        return cfg.BOT_USERNAME.strip().lstrip("@")
    me = await bot.get_me()
    return me.username or "playerok_bot"


@router.message(CommandStart(deep_link=False))
async def cmd_start(message: Message, state: FSMContext, session, bot: Bot):
    await state.clear()
    user = await usvc.get_user(session, message.from_user.id)
    if user and user.registered and user.nickname:
        await show_menu(message, session, user, state)
        return
    # создаём/обновляем пользователя без регистрации
    await usvc.ensure_user(session, message.from_user.id,
                           message.from_user.username, message.from_user.first_name)
    await state.set_state(Registration.phone)
    # Reply-клавиатура с request_contact возможна только на текстовом сообщении;
    # если задан баннер START — сначала показываем фото, затем текст с кнопкой контакта.
    from bot.services.catalog import get_banner
    banner = await get_banner(session, "START")
    if banner:
        await bot.send_photo(chat_id=message.chat.id, photo=banner,
                             caption=START_TEXT, parse_mode="HTML")
        await message.answer(
            "👇 <b>Подтвердите номер — нажмите кнопку ниже:</b>",
            reply_markup=REG_KB, parse_mode="HTML")
    else:
        await message.answer(START_TEXT, reply_markup=REG_KB, parse_mode="HTML")
    await state.update_data(_screen_msg=None)

@router.message(Registration.phone, F.contact)
async def reg_phone(message: Message, state: FSMContext, session, bot: Bot):
    phone = message.contact.phone_number.replace(" ", "")
    user = await usvc.ensure_user(session, message.from_user.id,
                                  message.from_user.username, message.from_user.first_name)
    user.phone = phone[:32]
    await session.commit()
    await state.set_state(Registration.nickname)
    support_url = await get_setting(session, "support_link", "https://t.me/PIayerokSupports")
    text = PHONE_OK_NICK_PROMPT.format(support=SUPPORT_USERNAME)
    kb = inl([[("🏠 В МЕНЮ", "nav:menu")]])
    await render_screen(bot, session, chat_id=message.chat.id, text=text, kb=kb,
                        banner_section=None, message_id=None, state=state)
    await state.update_data(_screen_msg=None)


@router.message(Registration.phone, ~F.contact)
async def reg_phone_hint(message: Message, state: FSMContext):
    await message.answer(
        "👇 <b>Нажмите кнопку «📱 ПОДТВЕРДИТЬ НОМЕР» ниже и отправьте свой контакт Telegram.</b>",
        reply_markup=REG_KB, parse_mode="HTML")


@router.message(Registration.nickname, F.text)
async def reg_nickname(message: Message, state: FSMContext, session, bot: Bot):
    nick = message.text.strip().lstrip("@")
    user = await usvc.get_user(session, message.from_user.id)
    if user is None:
        user = await usvc.ensure_user(session, message.from_user.id,
                                      message.from_user.username, message.from_user.first_name)
    ok = await usvc.set_nickname(session, user, nick)
    if not ok:
        await message.answer(
            "❌ <b>Никнейм недоступен.</b>\n\n"
            "<b>Используйте 3–20 символов: латиница, цифры, «_». "
            "Возможно, никнейм уже занят — попробуйте другой.</b>\n\n"
            "✏️ <b>Введите желаемый никнейм:</b>",
            parse_mode="HTML",
            reply_markup=inl([[("🏠 В МЕНЮ", "nav:menu")]]))
        return
    channel_url = await get_setting(session, "channel_link", "https://t.me/Playerok")
    await state.set_state(Registration.subscribe)
    await render_screen(bot, session, chat_id=message.chat.id, text=SUBSCRIBE_TEXT,
                        kb=subscribe_kb(channel_url), banner_section=None,
                        message_id=None, state=state)
    await state.update_data(_screen_msg=None)


async def finish_registration(call_or_msg, session, state: FSMContext, subscribed: bool):
    if isinstance(call_or_msg, CallbackQuery):
        tg_id = call_or_msg.from_user.id
        bot = call_or_msg.bot
        chat_id = call_or_msg.message.chat.id
        msg_id = call_or_msg.message.message_id
    else:
        tg_id = call_or_msg.from_user.id
        bot = call_or_msg.bot
        chat_id = call_or_msg.chat.id
        msg_id = None
    user = await usvc.get_user(session, tg_id)
    if user is None or not user.nickname:
        await state.set_state(Registration.nickname)
        kb = inl([[("🏠 В МЕНЮ", "nav:menu")]])
        await render_screen(bot, session, chat_id=chat_id,
                            text=PHONE_OK_NICK_PROMPT.format(support=SUPPORT_USERNAME),
                            kb=kb, message_id=msg_id, state=state)
        return
    user.subscribed = subscribed
    await usvc.complete_registration(session, user)
    await state.clear()
    text = REG_DONE_TEXT.format(nickname=user.nickname, internal_id=user.id)
    kb = inl([[("🏠 ГЛАВНОЕ МЕНЮ", "nav:menu")]])
    await render_screen(bot, session, chat_id=chat_id, text=text, kb=kb,
                        banner_section=None, message_id=msg_id, state=state)


@router.callback_query(Registration.subscribe, F.data == "reg:sub:done")
async def reg_sub_done(call: CallbackQuery, state: FSMContext, session):
    await call.answer()
    await finish_registration(call, session, state, True)


@router.callback_query(Registration.subscribe, F.data == "reg:sub:skip")
async def reg_sub_skip(call: CallbackQuery, state: FSMContext, session):
    await call.answer()
    await finish_registration(call, session, state, False)


@router.callback_query(F.data == "reg:sub:url")
async def reg_sub_url(call: CallbackQuery):
    await call.answer()


async def show_menu(message: Message, session, user, state: FSMContext):
    from bot.keyboard.kbs import main_menu_kb
    text = MENU_TEXT.format(
        nickname=user.nickname or user.first_name or "друг",
        balance=fmt_money(user.available_balance),
        frozen=fmt_money(user.frozen_balance))
    data = await state.get_data()
    await render_screen(message.bot, session, chat_id=message.chat.id, text=text,
                        kb=main_menu_kb(), banner_section="MENU",
                        message_id=data.get("_screen_msg"), state=state)
