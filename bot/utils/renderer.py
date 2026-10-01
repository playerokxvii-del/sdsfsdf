"""Рендер экранов: баннер + текст + inline-клавиатура, редактирование текущего сообщения."""
from __future__ import annotations

import logging

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.services.catalog import get_banner
from bot.utils.texts import err_text

log = logging.getLogger("playerok.render")


async def render_screen(bot: Bot, session: AsyncSession, *, chat_id: int,
                        text: str, kb, banner_section: str | None = None,
                        message_id: int | None = None, parse_mode: str = "HTML",
                        state: FSMContext | None = None):
    """
    Показывает экран.
    - Если у раздела есть баннер (file_id) — отправляем фото с подписью,
      старое сообщение экрана удаляем.
    - Иначе — редактируем текущее сообщение (message_id), при неудаче шлём новое.
    id показанного сообщения сохраняется в FSM data['_screen_msg'].
    """
    file_id = await get_banner(session, banner_section) if banner_section else None
    data = await state.get_data() if state else {}
    old_msg_id = data.get("_screen_msg")
    shown_id: int | None = None
    try:
        if file_id:
            sent = await bot.send_photo(chat_id=chat_id, photo=file_id, caption=text,
                                         reply_markup=kb, parse_mode=parse_mode)
            shown_id = sent.message_id
            for stale in filter(None, {old_msg_id, message_id}):
                if stale != shown_id:
                    try:
                        await bot.delete_message(chat_id, stale)
                    except TelegramAPIError:
                        pass  # не критично
        elif message_id is not None:
            try:
                await bot.edit_message_caption(
                    chat_id=chat_id, message_id=message_id, caption=text,
                    reply_markup=kb, parse_mode=parse_mode)
                shown_id = message_id
            except TelegramAPIError:
                try:
                    await bot.edit_message_text(
                        chat_id=chat_id, message_id=message_id, text=text,
                        reply_markup=kb, parse_mode=parse_mode)
                    shown_id = message_id
                except TelegramAPIError as e:
                    if "message is not modified" in str(e).lower():
                        shown_id = message_id
                    else:
                        log.debug("edit failed (%s); sending new", e.__class__.__name__)
                        sent = await bot.send_message(chat_id=chat_id, text=text,
                                                      reply_markup=kb, parse_mode=parse_mode)
                        shown_id = sent.message_id
                        if old_msg_id and old_msg_id != shown_id:
                            try:
                                await bot.delete_message(chat_id, old_msg_id)
                            except TelegramAPIError:
                                pass
        else:
            sent = await bot.send_message(chat_id=chat_id, text=text,
                                          reply_markup=kb, parse_mode=parse_mode)
            shown_id = sent.message_id
    except TelegramAPIError:
        log.exception("render_screen telegram error")
        sent = await bot.send_message(chat_id=chat_id, text=err_text(),
                                      reply_markup=kb, parse_mode="HTML")
        shown_id = sent.message_id
    if state is not None:
        await state.update_data(_screen_msg=shown_id)
    return shown_id


async def show(call: CallbackQuery | Message, session: AsyncSession, *, text: str,
               kb, banner: str | None = None, state: FSMContext | None = None):
    """Обёртка: показать экран в том же сообщении (callback) или новом (message)."""
    if isinstance(call, CallbackQuery):
        bot = call.bot
        chat_id = call.message.chat.id
        msg_id = call.message.message_id
    else:
        bot = call.bot
        chat_id = call.chat.id
        msg_id = None
    return await render_screen(bot, session, chat_id=chat_id, text=text, kb=kb,
                               banner_section=banner, message_id=msg_id, state=state)
