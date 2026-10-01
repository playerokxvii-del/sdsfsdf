"""Админ: категории/игры, настройки, баннеры, новости, рассылка."""
from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select

from bot.admin.main import guard
from bot.db.base import get_setting, set_setting
from bot.db.models import BANNER_SECTIONS, Category, Game, NewsPost
from bot.handlers.deal_create import esc
from bot.keyboard.kbs import inl
from bot.services import catalog
from bot.states.states import AdminStates
from bot.utils.renderer import render_screen, show
from bot.utils.texts import LINE, fmt_money

log = logging.getLogger("playerok.admin2")
router = Router(name="admin_catalog_settings")


# ================= КАТЕГОРИИ =================

@router.callback_query(F.data == "ad:cats")
async def ad_cats(call: CallbackQuery, state: FSMContext, session):
    if not await guard(call, session):
        return
    await call.answer()
    cats = await catalog.all_categories(session)
    rows = []
    for c in cats[:18]:
        mark = "✅" if c.active else "🚫"
        rows.append([(f"{mark} {c.emoji} {c.name[:22]}", f"adc:man:{c.id}")])
    rows.append([("➕ ДОБАВИТЬ КАТЕГОРИЮ", "adc:add"), ("🎮 ИГРЫ", "adc:games_hint")])
    rows.append([("🔄 ОБНОВИТЬ", "ad:cats")])
    rows.append([("🔙 В АДМИНКУ", "ad:home")])
    lines = [f"<b>{'✅' if c.active else '🚫'} {c.sort_order}. {c.emoji} {esc(c.name)}"
            f"{' · 🎮 игры' if c.requires_game else ''}</b>" for c in cats]
    text = (f"📂 <b>КАТЕГОРИИ ({len(cats)})</b>\n\n{LINE}\n\n" + "\n".join(lines))
    await show(call, session, text=text, kb=inl(rows), banner="ADMIN", state=state)


@router.callback_query(F.data == "adc:games_hint")
async def adc_games_hint(call: CallbackQuery, state: FSMContext, session):
    await call.answer()
    cats = await catalog.all_categories(session)
    rows = [[(f"🎮 {c.name[:24]}", f"adc:catgames:{c.id}")] for c in cats[:18]]
    rows.append([("🔙 К КАТЕГОРИЯМ", "ad:cats")])
    await show(call, session,
               text=f"🎮 <b>УПРАВЛЕНИЕ ИГРАМИ</b>\n\n{LINE}\n\n"
                    "<b>Выберите категорию, чтобы управлять списком игр:</b>",
               kb=inl(rows), state=state)


@router.callback_query(F.data.startswith("adc:catgames:"))
async def adc_catgames(call: CallbackQuery, state: FSMContext, session):
    if not await guard(call, session):
        return
    cat_id = int(call.data.split(":")[2])
    games = await catalog.category_games(session, cat_id)
    await call.answer()
    rows = [[(g.name[:26], f"adg:man:{g.id}")] for g in games[:20]]
    if len(games) > 20:
        rows.append([(f"… всего {len(games)}", "noop:none")])
    rows.append([("➕ ДОБАВИТЬ ИГРУ", f"adg:add:{cat_id}")])
    rows.append([("🔙 К ИГРАМ", "adc:games_hint"), ("🏠 В АДМИНКУ", "ad:home")])
    from bot.db.models import Category as C
    cat = await session.get(C, cat_id)
    text = (f"🎮 <b>ИГРЫ: {esc(cat.name if cat else '?')}</b>\n\n{LINE}\n\n" +
            "\n".join(f"<b>• {esc(g.name)}{' 🚫' if not g.active else ''}</b>" for g in games[:30]))
    await show(call, session, text=text, kb=inl(rows), state=state)


@router.callback_query(F.data.startswith("adg:add:"))
async def adg_add(call: CallbackQuery, state: FSMContext, session):
    if not await guard(call, session):
        return
    cat_id = int(call.data.split(":")[2])
    await call.answer()
    await state.set_state(AdminStates.game_add)
    await state.update_data(game_add={"cat_id": cat_id})
    await show(call, session,
               text=f"➕ <b>НОВАЯ ИГРА</b>\n\n{LINE}\n\n<b>Отправьте название игры:</b>",
               kb=inl([[("❌ ОТМЕНА", "adc:games_hint")]]), state=state)


@router.message(AdminStates.game_add, F.text)
async def adg_add_apply(message: Message, state: FSMContext, session):
    if not await guard(message, session):
        return
    data = await state.get_data()
    cat_id = data.get("game_add", {}).get("cat_id")
    g = await catalog.add_game(session, cat_id, message.text.strip()) if cat_id else None
    await state.clear()
    if g is None:
        await message.answer("❌ <b>Не удалось добавить игру.</b>", parse_mode="HTML")
        return
    await catalog.log_admin_action(session, message.from_user.id, "ADD_GAME",
                                   f"category:{cat_id}", None, g.name)
    await message.answer(f"✅ <b>Игра «{esc(g.name)}» добавлена.</b>", parse_mode="HTML")


@router.callback_query(F.data.startswith("adg:man:"))
async def adg_manage(call: CallbackQuery, state: FSMContext, session):
    if not await guard(call, session):
        return
    gid = int(call.data.split(":")[2])
    g = await session.get(Game, gid)
    await call.answer()
    if g is None:
        await ad_cats(call, state, session)
        return
    kb = inl([
        [("🚫 ОТКЛЮЧИТЬ" if g.active else "✅ ВКЛЮЧИТЬ", f"adg:toggle:{g.id}")],
        [("🔀 ВЫШЕ", f"adg:up:{g.id}"), ("🔀 НИЖЕ", f"adg:down:{g.id}")],
        [("🗑 УДАЛИТЬ", f"adg:del:{g.id}")],
        [("🔙 НАЗАД", f"adc:catgames:{g.category_id}")],
    ])
    await show(call, session,
               text=f"🎮 <b>ИГРА: {esc(g.name)}</b>\n\n{LINE}\n\n"
                    f"<b>Категория ID: {g.category_id} · Порядок: {g.sort_order} · "
                    f"Статус: {'активна' if g.active else 'отключена'}</b>",
               kb=kb, state=state)


@router.callback_query(F.data.startswith("adg:toggle:"))
async def adg_toggle(call: CallbackQuery, state: FSMContext, session):
    if not await guard(call, session):
        return
    gid = int(call.data.split(":")[2])
    g = await session.get(Game, gid)
    if g:
        g.active = not g.active
        await session.commit()
        await catalog.log_admin_action(session, call.from_user.id, "TOGGLE_GAME",
                                       f"game:{g.name}", None, str(g.active))
    await call.answer("✅ Готово")
    if g:
        call.data = f"adg:man:{gid}"
        await adg_manage(call, state, session)
    else:
        await adc_games_hint(call, state, session)


@router.callback_query(F.data.startswith("adg:del:"))
async def adg_del(call: CallbackQuery, state: FSMContext, session):
    if not await guard(call, session):
        return
    gid = int(call.data.split(":")[2])
    g = await session.get(Game, gid)
    if g:
        cid = g.category_id
        await session.delete(g)
        await session.commit()
        await catalog.log_admin_action(session, call.from_user.id, "DEL_GAME",
                                       f"game:{g.name}", g.name, None)
        await call.answer("🗑 Удалено")
        await adc_catgames_by(call, state, session, cid)
    else:
        await call.answer("Не найдено", show_alert=True)


async def adc_catgames_by(call, state, session, cat_id):
    call.data = f"adc:catgames:{cat_id}"
    await adc_catgames(call, state, session)


@router.callback_query(F.data.startswith("adg:up:"))
@router.callback_query(F.data.startswith("adg:down:"))
async def adg_move(call: CallbackQuery, state: FSMContext, session):
    if not await guard(call, session):
        return
    action, gid = call.data.split(":")[1], int(call.data.split(":")[2])
    g = await session.get(Game, gid)
    if g:
        stmt = select(Game).where(Game.category_id == g.category_id) \
            .order_by(Game.sort_order.asc())
        games = list((await session.execute(stmt)).scalars().all())
        idx = next((i for i, x in enumerate(games) if x.id == gid), None)
        j = idx + (-1 if action == "up" else 1)
        if idx is not None and 0 <= j < len(games):
            games[idx].sort_order, games[j].sort_order = j, idx
            await session.commit()
    await call.answer("🔀 Порядок обновлён")
    await adg_manage(call, state, session)


# ---------- Управление категорией ----------

@router.callback_query(F.data.startswith("adc:man:"))
async def adc_manage(call: CallbackQuery, state: FSMContext, session):
    if not await guard(call, session):
        return
    cid = int(call.data.split(":")[2])
    c = await session.get(Category, cid)
    await call.answer()
    if c is None:
        await ad_cats(call, state, session)
        return
    kb = inl([
        [("🚫 ОТКЛЮЧИТЬ" if c.active else "✅ ВКЛЮЧИТЬ", f"adc:toggle:{c.id}")],
        [("✏️ ПЕРЕИМЕНОВАТЬ", f"adc:rename:{c.id}")],
        [("🔀 ВЫШЕ", f"adc:up:{c.id}"), ("🔀 НИЖЕ", f"adc:down:{c.id}")],
        [("🎮 ИГРЫ КАТЕГОРИИ", f"adc:catgames:{c.id}")],
        [("🗑 УДАЛИТЬ", f"adc:del:{c.id}")],
        [("🔙 К КАТЕГОРИЯМ", "ad:cats"), ("🏠 В АДМИНКУ", "ad:home")],
    ])
    await show(call, session,
               text=f"📂 <b>КАТЕГОРИЯ: {c.emoji} {esc(c.name)}</b>\n\n{LINE}\n\n"
                    f"<b>ID: {c.id} · Порядок: {c.sort_order} · "
                    f"{'✅ Активна' if c.active else '🚫 Отключена'} · "
                    f"{'🎮 требует игру' if c.requires_game else '— игра не требуется'}</b>",
               kb=kb, state=state)


@router.callback_query(F.data.startswith("adc:toggle:"))
async def adc_toggle(call: CallbackQuery, state: FSMContext, session):
    if not await guard(call, session):
        return
    cid = int(call.data.split(":")[2])
    c = await session.get(Category, cid)
    if c:
        c.active = not c.active
        await session.commit()
        await catalog.log_admin_action(session, call.from_user.id, "TOGGLE_CATEGORY",
                                       f"category:{c.name}", None, str(c.active))
    await call.answer("✅ Готово")
    await ad_cats(call, state, session)


@router.callback_query(F.data.startswith("adc:del:"))
async def adc_del(call: CallbackQuery, state: FSMContext, session):
    if not await guard(call, session):
        return
    cid = int(call.data.split(":")[2])
    c = await session.get(Category, cid)
    name = c.name if c else "?"
    if c:
        await catalog.delete_category(session, cid)
        await catalog.log_admin_action(session, call.from_user.id, "DEL_CATEGORY",
                                       f"category:{name}", name, None)
    await call.answer("🗑 Удалено")
    await ad_cats(call, state, session)


@router.callback_query(F.data.startswith("adc:rename:"))
async def adc_rename(call: CallbackQuery, state: FSMContext, session):
    if not await guard(call, session):
        return
    cid = int(call.data.split(":")[2])
    await call.answer()
    await state.set_state(AdminStates.cat_rename)
    await state.update_data(cat_rename={"cid": cid})
    await show(call, session,
               text=f"✏️ <b>ПЕРЕИМЕНОВАНИЕ КАТЕГОРИИ #{cid}</b>\n\n{LINE}\n\n"
                    "<b>Отправьте новое название:</b>",
               kb=inl([[("❌ ОТМЕНА", "ad:cats")]]), state=state)


@router.message(AdminStates.cat_rename, F.text)
async def adc_rename_apply(message: Message, state: FSMContext, session):
    if not await guard(message, session):
        return
    data = await state.get_data()
    cid = data.get("cat_rename", {}).get("cid")
    ok = await catalog.rename_category(session, cid, message.text.strip()) if cid else False
    await state.clear()
    await message.answer("✅ <b>Переименовано.</b>" if ok else "❌ <b>Не удалось.</b>",
                         parse_mode="HTML")


@router.callback_query(F.data == "adc:add")
async def adc_add(call: CallbackQuery, state: FSMContext, session):
    if not await guard(call, session):
        return
    await call.answer()
    await state.set_state(AdminStates.cat_add_name)
    await show(call, session,
               text=f"➕ <b>НОВАЯ КАТЕГОРИЯ</b>\n\n{LINE}\n\n"
                    "<b>Отправьте название и эмодзи через пробел,</b>\n"
                    "<b>например: 💳 Банковские карты</b>\n\n"
                    "<b>Для игровой категории добавьте слово «игра», например:</b>\n"
                    "<b>🎮 Новинки игра</b>",
               kb=inl([[("❌ ОТМЕНА", "ad:cats")]]), state=state)


@router.message(AdminStates.cat_add_name, F.text)
async def adc_add_apply(message: Message, state: FSMContext, session):
    if not await guard(message, session):
        return
    txt = message.text.strip()
    requires_game = False
    if txt.lower().endswith(" игра") or txt.lower().endswith(" game"):
        requires_game = True
        txt = txt.rsplit(" ", 1)[0]
    parts = txt.split(maxsplit=1)
    emoji, name = (parts[0], parts[1]) if len(parts) == 2 else ("📦", txt)
    cat = await catalog.add_category(session, name, emoji, requires_game)
    await state.clear()
    if cat is None:
        await message.answer("❌ <b>Категория с таким названием уже существует или пуста.</b>",
                             parse_mode="HTML")
        return
    await catalog.log_admin_action(session, message.from_user.id, "ADD_CATEGORY",
                                   f"category:{cat.name}", None, cat.name)
    hint = ("\n\n🎮 <b>Добавьте игры кнопкой «🎮 ИГРЫ» → «➕ ДОБАВИТЬ ИГРУ».</b>"
            if requires_game else "")
    await message.answer(f"✅ <b>Категория «{esc(cat.name)}» добавлена.</b>{hint}",
                         parse_mode="HTML")


@router.callback_query(F.data.startswith("adc:up:"))
@router.callback_query(F.data.startswith("adc:down:"))
async def adc_move(call: CallbackQuery, state: FSMContext, session):
    if not await guard(call, session):
        return
    action, cid = call.data.split(":")[1], int(call.data.split(":")[2])
    await catalog.move_category(session, cid, -1 if action == "up" else 1)
    await call.answer("🔀 Порядок обновлён")
    await ad_cats(call, state, session)


@router.callback_query(F.data == "adc:hint:order")
async def adc_hint(call: CallbackQuery):
    await call.answer("Порядок меняется кнопками 🔀 ВЫШЕ/НИЖЕ внутри категории",
                      show_alert=True)


@router.callback_query(F.data == "noop:none")
async def noop_none(call: CallbackQuery):
    await call.answer("Список длинный — управляйте через поиск/добавление", show_alert=True)


# ================= НАСТРОЙКИ =================

SETTINGS_ITEMS = [
    ("commission_percent", "💸 КОМИССИЯ %", "set:commission"),
    ("payment_requisites", "🧾 РЕКВИЗИТЫ", "set:requisites"),
    ("min_deposit", "📉 МИН. ПОПОЛНЕНИЕ", "set:min_deposit"),
    ("min_withdraw", "📉 МИН. ВЫВОД", "set:min_withdraw"),
    ("channel_link", "📢 ССЫЛКА КАНАЛА", "set:channel"),
    ("support_link", "🆘 ССЫЛКА ПОДДЕРЖКИ", "set:support"),
]


@router.callback_query(F.data == "ad:settings")
async def ad_settings(call: CallbackQuery, state: FSMContext, session):
    if not await guard(call, session):
        return
    await call.answer()
    maint = (await get_setting(session, "maintenance", "0")) == "1"
    vals = []
    for key, label, cb in SETTINGS_ITEMS:
        v = await get_setting(session, key, "")
        shown = v if len(v) <= 40 else v[:37] + "…"
        vals.append(f"<b>{label}: {esc(shown) or '—'}</b>")
    admins = await get_setting(session, "admin_ids", "")
    text = (f"⚙️ <b>НАСТРОЙКИ СИСТЕМЫ</b>\n\n{LINE}\n\n" + "\n".join(vals) +
            f"\n\n👨‍💼 <b>Доп. админы: {esc(admins) or 'нет'}</b>\n\n"
            f"🛠 <b>Технические работы: {'ВКЛЮЧЕНЫ 🛑' if maint else 'выключены ✅'}</b>")
    kb = inl([
        [(("🛑 ВКЛ. ТЕХРАБОТЫ" if not maint else "✅ ВЫКЛ. ТЕХРАБОТЫ"), "set:maint")],
        [("💸 Комиссия", "set:commission"), ("🧾 Реквизиты", "set:requisites")],
        [("📉 Мин. пополнение", "set:min_deposit"), ("📉 Мин. вывод", "set:min_withdraw")],
        [("📢 Канал", "set:channel"), ("🆘 Поддержка", "set:support")],
        [("👨‍💼 АДМИНЫ", "set:admins")],
        [("🔙 В АДМИНКУ", "ad:home")],
    ])
    await show(call, session, text=text, kb=kb, banner="ADMIN", state=state)


SET_STATE_MAP = {
    "set:commission": (AdminStates.set_commission, "commission_percent"),
    "set:requisites": (AdminStates.set_requisites, "payment_requisites"),
    "set:min_deposit": (AdminStates.set_min_deposit, "min_deposit"),
    "set:min_withdraw": (AdminStates.set_min_withdraw, "min_withdraw"),
    "set:channel": (AdminStates.set_channel, "channel_link"),
    "set:support": (AdminStates.set_support, "support_link"),
    "set:admins": (AdminStates.set_recipient, "admin_ids"),
}

SET_PROMPTS = {
    "commission_percent": ("💸 <b>КОМИССИЯ</b>", "<b>Отправьте число процентов (0–90), напр.: 10</b>"),
    "payment_requisites": ("🧾 <b>РЕКВИЗИТЫ</b>", "<b>Отправьте новый текст реквизитов для пополнения.</b>"),
    "min_deposit": ("📉 <b>МИНИМУМ ПОПОЛНЕНИЯ</b>", "<b>Отправьте число, напр.: 50</b>"),
    "min_withdraw": ("📉 <b>МИНИМУМ ВЫВОДА</b>", "<b>Отправьте число, напр.: 100</b>"),
    "channel_link": ("📢 <b>ССЫЛКА КАНАЛА</b>", "<b>Отправьте ссылку, напр.: https://t.me/Playerok</b>"),
    "support_link": ("🆘 <b>ССЫЛКА ПОДДЕРЖКИ</b>", "<b>Отправьте ссылку, напр.: https://t.me/PIayerokSupports</b>"),
    "admin_ids": ("👨‍💼 <b>ДОПОЛНИТЕЛЬНЫЕ АДМИНЫ</b>",
                  "<b>Отправьте Telegram ID через запятую, напр.: 123456,789012.\n"
                  "Отправьте «пусто», чтобы очистить.</b>"),
}


@router.callback_query(F.data.in_(list(SET_STATE_MAP.keys())))
async def set_item_start(call: CallbackQuery, state: FSMContext, session):
    if not await guard(call, session):
        return
    st_key, setting_key = SET_STATE_MAP[call.data]
    await call.answer()
    await state.set_state(st_key)
    title, body = SET_PROMPTS[setting_key]
    await show(call, session,
               text=f"{title}\n\n{LINE}\n\n{body}",
               kb=inl([[("❌ ОТМЕНА", "ad:settings")]]), state=state)


@router.callback_query(F.data == "set:maint")
async def set_maint(call: CallbackQuery, state: FSMContext, session):
    if not await guard(call, session):
        return
    cur = (await get_setting(session, "maintenance", "0")) == "1"
    new = "0" if cur else "1"
    await set_setting(session, "maintenance", new)
    await catalog.log_admin_action(session, call.from_user.id, "MAINTENANCE",
                                   "system", str(cur), str(new == "1"))
    await call.answer("🛠 Режим техработ переключён")
    await ad_settings(call, state, session)


async def apply_setting(message: Message, state: FSMContext, session,
                        setting_key: str, value: str, validate=None):
    old = await get_setting(session, setting_key, "")
    if validate:
        err = validate(value)
        if err:
            await message.answer(f"❌ <b>{err}</b>", parse_mode="HTML")
            return
    await set_setting(session, setting_key, value)
    await catalog.log_admin_action(session, message.from_user.id, "SET_" + setting_key.upper(),
                                   setting_key, old[:100], value[:100])
    await state.clear()
    await message.answer(f"✅ <b>Настройка «{setting_key}» обновлена.</b>", parse_mode="HTML")


def _num_validator(lo, hi):
    def check(v):
        try:
            d = DecimalOk(v)
        except Exception:
            return "Введите корректное число."
        if not (lo <= d <= hi):
            return f"Значение должно быть от {lo} до {hi}."
        return None
    return check


from decimal import Decimal as DecimalOk

@router.message(AdminStates.set_commission, F.text)
async def s_commission(message: Message, state: FSMContext, session):
    if not await guard(message, session):
        return
    await apply_setting(message, state, session, "commission_percent", message.text.strip(),
                        validate=_num_validator(0, 90))


@router.message(AdminStates.set_requisites, F.text)
async def s_req(message: Message, state: FSMContext, session):
    if not await guard(message, session):
        return
    await apply_setting(message, state, session, "payment_requisites", message.text.strip()[:2000])


@router.message(AdminStates.set_min_deposit, F.text)
async def s_min_dep(message: Message, state: FSMContext, session):
    if not await guard(message, session):
        return
    await apply_setting(message, state, session, "min_deposit", message.text.strip(),
                        validate=_num_validator(1, 1000000))


@router.message(AdminStates.set_min_withdraw, F.text)
async def s_min_wdr(message: Message, state: FSMContext, session):
    if not await guard(message, session):
        return
    await apply_setting(message, state, session, "min_withdraw", message.text.strip(),
                        validate=_num_validator(1, 1000000))


@router.message(AdminStates.set_channel, F.text)
async def s_channel(message: Message, state: FSMContext, session):
    if not await guard(message, session):
        return
    await apply_setting(message, state, session, "channel_link", message.text.strip()[:300],
                        validate=lambda v: None if v.startswith(("http://", "https://", "t.me/", "@"))
                        else "Ссылка должна начинаться с http(s):// или t.me/")


@router.message(AdminStates.set_support, F.text)
async def s_support(message: Message, state: FSMContext, session):
    if not await guard(message, session):
        return
    await apply_setting(message, state, session, "support_link", message.text.strip()[:300],
                        validate=lambda v: None if v.startswith(("http://", "https://", "t.me/", "@"))
                        else "Ссылка должна начинаться с http(s):// или t.me/")


@router.message(AdminStates.set_recipient, F.text)
async def s_admins(message: Message, state: FSMContext, session):
    if not await guard(message, session):
        return
    v = message.text.strip()
    if v.lower() in {"пусто", "none", "-"}:
        v = ""
    cleaned = ",".join(x for x in v.replace(" ", "").split(",") if x.isdigit())
    await apply_setting(message, state, session, "admin_ids", cleaned)


# ================= БАННЕРЫ =================

BANNER_LABELS = {
    "START": "🚀 СТАРТ", "MENU": "🏠 МЕНЮ", "CREATE_DEAL": "➕ СОЗДАНИЕ СДЕЛКИ",
    "DEALS": "📋 СДЕЛКИ", "WALLET": "💰 КОШЕЛЁК", "PROFILE": "👤 ПРОФИЛЬ",
    "FAQ": "❓ FAQ", "SUPPORT": "🆘 ПОДДЕРЖКА", "NEWS": "📢 НОВОСТИ",
    "ADMIN": "🛠 АДМИНКА",
}


@router.callback_query(F.data == "ad:banners")
async def ad_banners(call: CallbackQuery, state: FSMContext, session):
    if not await guard(call, session):
        return
    await call.answer()
    rows = []
    status_lines = []
    for section in BANNER_SECTIONS:
        fid = await catalog.get_banner(session, section)
        rows.append([(BANNER_LABELS[section], f"adb:set:{section}")])
        status_lines.append(f"<b>{BANNER_LABELS[section]}: "
                            f"{'🖼 установлен' if fid else '— нет'}</b>")
    text = (f"🖼 <b>БАННЕРЫ РАЗДЕЛОВ</b>\n\n{LINE}\n\n" + "\n".join(status_lines) +
            "\n\n<b>Выберите раздел и отправьте картинку — она станет баннером экрана.</b>")
    rows.append([("🔙 В АДМИНКУ", "ad:home")])
    await show(call, session, text=text, kb=inl(rows), banner="ADMIN", state=state)


@router.callback_query(F.data.startswith("adb:set:"))
async def adb_set(call: CallbackQuery, state: FSMContext, session):
    if not await guard(call, session):
        return
    section = call.data.split(":")[2]
    await call.answer()
    await state.set_state(AdminStates.banner_file)
    await state.update_data(banner={"section": section})
    await show(call, session,
               text=f"🖼 <b>БАННЕР: {BANNER_LABELS.get(section, section)}</b>\n\n{LINE}\n\n"
                    "<b>Отправьте изображение (photo).</b>\n\n"
                    "<b>Чтобы удалить баннер — отправьте слово «удалить».</b>",
               kb=inl([[("❌ ОТМЕНА", "ad:banners")]]), state=state)


@router.message(AdminStates.banner_file, F.photo)
async def adb_photo(message: Message, state: FSMContext, session):
    if not await guard(message, session):
        return
    data = await state.get_data()
    section = data.get("banner", {}).get("section")
    file_id = message.photo[-1].file_id
    if section:
        await catalog.set_banner(session, section, file_id)
        await catalog.log_admin_action(session, message.from_user.id, "SET_BANNER",
                                       f"banner:{section}", None, file_id[:40])
    await state.clear()
    await message.answer("✅ <b>Баннер обновлён!</b>", parse_mode="HTML")


@router.message(AdminStates.banner_file, F.text)
async def adb_remove(message: Message, state: FSMContext, session):
    if not await guard(message, session):
        return
    data = await state.get_data()
    section = data.get("banner", {}).get("section")
    if message.text.strip().lower() in {"удалить", "delete", "-"} and section:
        await catalog.set_banner(session, section, "")
        await catalog.log_admin_action(session, message.from_user.id, "DEL_BANNER",
                                       f"banner:{section}", "set", "")
        await message.answer("🗑 <b>Баннер удалён.</b>", parse_mode="HTML")
    else:
        await message.answer("⚠️ <b>Отправьте изображение или слово «удалить».</b>",
                             parse_mode="HTML")
        return
    await state.clear()


# ================= НОВОСТИ =================

@router.callback_query(F.data == "ad:news")
async def ad_news(call: CallbackQuery, state: FSMContext, session):
    if not await guard(call, session):
        return
    await call.answer()
    posts = await catalog.published_news(session, 15)
    rows = [[(f"📰 {p.title[:28]}", f"adn:view:{p.id}")] for p in posts[:12]]
    rows.append([("✍️ ОПУБЛИКОВАТЬ НОВОСТЬ", "adn:new")])
    rows.append([("🔙 В АДМИНКУ", "ad:home")])
    lines = [f"<b>📰 {esc(p.title)} — {p.created_at.strftime('%d.%m.%Y')}</b>" for p in posts]
    text = (f"📰 <b>НОВОСТИ</b>\n\n{LINE}\n\n" + ("\n".join(lines) or "<b>Пусто</b>"))
    await show(call, session, text=text, kb=inl(rows), banner="ADMIN", state=state)


@router.callback_query(F.data == "adn:new")
async def adn_new(call: CallbackQuery, state: FSMContext, session):
    if not await guard(call, session):
        return
    await call.answer()
    await state.set_state(AdminStates.news_title)
    await show(call, session,
               text=f"✍️ <b>НОВАЯ НОВОСТЬ</b>\n\n{LINE}\n\n<b>Шаг 1. Отправьте заголовок:</b>",
               kb=inl([[("❌ ОТМЕНА", "ad:news")]]), state=state)


@router.message(AdminStates.news_title, F.text)
async def adn_title(message: Message, state: FSMContext, session):
    if not await guard(message, session):
        return
    await state.set_state(AdminStates.news_text)
    await state.update_data(news={"title": message.text.strip()[:200]})
    await render_screen(message.bot, session, chat_id=message.chat.id,
                        text=f"✍️ <b>НОВАЯ НОВОСТЬ</b>\n\n{LINE}\n\n<b>Шаг 2. Отправьте текст новости:</b>",
                        kb=inl([[("❌ ОТМЕНА", "ad:news")]]), state=state)


@router.message(AdminStates.news_text, F.text)
async def adn_text(message: Message, state: FSMContext, session):
    if not await guard(message, session):
        return
    data = await state.get_data()
    news = data.get("news", {})
    post = NewsPost(title=news.get("title", "Новость"),
                    text=message.text.strip()[:3000], published=True)
    session.add(post)
    await session.commit()
    await catalog.log_admin_action(session, message.from_user.id, "PUBLISH_NEWS",
                                   f"news:{post.id}", None, post.title)
    await state.clear()
    await message.answer(f"✅ <b>Новость «{esc(post.title)}» опубликована!</b>",
                         parse_mode="HTML")


@router.callback_query(F.data.startswith("adn:view:"))
async def adn_view(call: CallbackQuery, state: FSMContext, session):
    if not await guard(call, session):
        return
    pid = int(call.data.split(":")[2])
    post = await session.get(NewsPost, pid)
    await call.answer()
    if post is None:
        await ad_news(call, state, session)
        return
    kb = inl([
        [("🚫 СНЯТЬ С ПУБЛИКАЦИИ" if post.published else "✅ ОПУБЛИКОВАТЬ",
          f"adn:toggle:{post.id}")],
        [("🗑 УДАЛИТЬ", f"adn:del:{post.id}")],
        [("🔙 К НОВОСТЯМ", "ad:news"), ("🏠 В АДМИНКУ", "ad:home")],
    ])
    await show(call, session,
               text=f"📰 <b>{esc(post.title)}</b>\n\n{LINE}\n\n<b>{esc(post.text[:900])}</b>",
               kb=kb, state=state)


@router.callback_query(F.data.startswith("adn:toggle:"))
async def adn_toggle(call: CallbackQuery, state: FSMContext, session):
    if not await guard(call, session):
        return
    post = await session.get(NewsPost, int(call.data.split(":")[2]))
    if post:
        post.published = not post.published
        await session.commit()
    await call.answer("✅ Готово")
    await ad_news(call, state, session)


@router.callback_query(F.data.startswith("adn:del:"))
async def adn_del(call: CallbackQuery, state: FSMContext, session):
    if not await guard(call, session):
        return
    post = await session.get(NewsPost, int(call.data.split(":")[2]))
    if post:
        await session.delete(post)
        await session.commit()
    await call.answer("🗑 Удалено")
    await ad_news(call, state, session)
