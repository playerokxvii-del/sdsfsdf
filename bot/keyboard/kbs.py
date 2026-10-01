"""Клавиатуры Playerok. Все callback-префиксы имеют обработчики."""
from aiogram.types import (
    InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup,
)


def inl(rows: list[list[tuple[str, str]]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t, callback_data=c) for t, c in row] for row in rows
    ])


def url_btn(text: str, url: str) -> dict:
    return {"text": text, "url": url}


HOME = [("🏠 В МЕНЮ", "nav:menu")]
BACK = [("🔙 НАЗАД", "back")]

# ---------- Регистрация ----------

REG_KB = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text="📱 ПОДТВЕРДИТЬ НОМЕР", request_contact=True)]],
    resize_keyboard=True, one_time_keyboard=True,
)

SUBSCRIBE_KB = None  # inline ниже


def subscribe_kb(channel_url: str) -> InlineKeyboardMarkup:
    kb = inl([[("✅ Я УЖЕ ПОДПИСАЛСЯ", "reg:sub:done")],
              [("➡️ ПРОДОЛЖИТЬ БЕЗ ПОДПИСКИ", "reg:sub:skip")]])
    kb.inline_keyboard.insert(0, [InlineKeyboardButton(
        text="📢 ПОДПИСАТЬСЯ НА @Playerok", url=channel_url)])
    return kb


DONE_REG_BTN = inl([HOME])

# ---------- Главное меню ----------

def main_menu_kb() -> InlineKeyboardMarkup:
    return inl([
        [("➕ СОЗДАТЬ СДЕЛКУ", "deal:new")],
        [("📋 МОИ СДЕЛКИ", "deals:menu")],
        [("💰 КОШЕЛЁК", "wallet:menu")],
        [("👤 ПРОФИЛЬ", "profile:me")],
        [("❓ FAQ", "faq:main"), ("📢 НОВОСТИ", "news:list")],
        [("🆘 ПОДДЕРЖКА", "support:menu")],
    ])


# ---------- Создание сделки ----------

def categories_kb(cats: list) -> InlineKeyboardMarkup:
    rows = []
    row = []
    for c in cats:
        row.append((f"{c.emoji} {c.name}", f"dc:cat:{c.id}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append(BACK)
    rows.append(HOME)
    return inl(rows)


def games_kb(games: list, cat_id: int) -> InlineKeyboardMarkup:
    rows = []
    row = []
    for g in games:
        row.append((g.name, f"dc:game:{cat_id}:{g.id}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([("🎮 ДРУГАЯ ИГРА ✏️", f"dc:game:{cat_id}:custom")])
    rows.append(BACK)
    rows.append(HOME)
    return inl(rows)


DEAL_CANCEL_ROWS = [[("❌ ОТМЕНА", "dc:cancel")]]


def preview_kb() -> InlineKeyboardMarkup:
    return inl([
        [("✅ СОЗДАТЬ СДЕЛКУ", "dc:create")],
        [("✏️ ИЗМЕНИТЬ", "dc:edit")],
        [("❌ ОТМЕНА", "dc:cancel")],
    ])


# ---------- Мои сделки ----------

def deals_menu_kb() -> InlineKeyboardMarkup:
    return inl([
        [("🟢 АКТИВНЫЕ", "deals:tab:active")],
        [("✅ ЗАВЕРШЁННЫЕ", "deals:tab:completed")],
        [("❌ ОТМЕНЁННЫЕ", "deals:tab:cancelled")],
        [("⚖️ СПОРЫ", "deals:tab:disputes")],
        HOME,
    ])


def deals_list_kb(deals: list, tab: str) -> InlineKeyboardMarkup:
    rows = [[(f"🆔 #{d.code} · {d.price:.0f} ₽", f"deal:view:{d.id}")] for d in deals[:20]]
    rows.append([("🔙 К РАЗДЕЛАМ", f"deals:tab:{tab}")])
    rows.append(BACK)
    rows.append(HOME)
    return inl(rows)


# ---------- Карточка сделки ----------

def deal_buyer_kb(deal_id: int) -> InlineKeyboardMarkup:
    return inl([
        [("💳 ОПЛАТИТЬ", f"pay:start:{deal_id}")],
        [("👤 ПРОФИЛЬ ПРОДАВЦА", f"user:view:{deal_id}")],
        [("🆘 ПОДДЕРЖКА", "support:menu")],
        HOME,
    ])


def deal_pay_confirm_kb(deal_id: int) -> InlineKeyboardMarkup:
    return inl([
        [("✅ ПОДТВЕРДИТЬ ОПЛАТУ", f"pay:confirm:{deal_id}")],
        [("🔙 НАЗАД К СДЕЛКЕ", f"deal:view:{deal_id}")],
        HOME,
    ])


def deal_seller_kb(deal_id: int, status: str) -> InlineKeyboardMarkup:
    rows = []
    if status in ("CREATED", "WAITING_FOR_BUYER", "BUYER_OPENED", "WAITING_PAYMENT"):
        rows.append([("🔗 ССЫЛКА НА СДЕЛКУ", f"deal:link:{deal_id}")])
        rows.append([("❌ ОТМЕНИТЬ СДЕЛКУ", f"deal:cancel:{deal_id}")])
    if status == "WAITING_MODERATION":
        rows.append([("⚖️ СОЗДАТЬ СПОР", f"disp:start:{deal_id}")])
    if status == "COMPLETED":
        rows.append([("⭐ ОСТАВИТЬ ОТЗЫВ", f"rev:start:{deal_id}")])
    if status in ("DISPUTED",):
        rows.append([("ℹ️ СТАТУС СПОРА", f"deal:view:{deal_id}")])
    rows.append(BACK)
    rows.append(HOME)
    return inl(rows)


def deal_buyer_active_kb(deal_id: int, status: str) -> InlineKeyboardMarkup:
    rows = []
    if status == "WAITING_MODERATION":
        rows.append([("⚖️ СОЗДАТЬ СПОР", f"disp:start:{deal_id}")])
    if status == "COMPLETED":
        rows.append([("⭐ ОСТАВИТЬ ОТЗЫВ", f"rev:start:{deal_id}")])
    rows.append(BACK)
    rows.append(HOME)
    return inl(rows)


def deal_done_kb(deal_id: int) -> InlineKeyboardMarkup:
    return inl([
        [("⭐ ОСТАВИТЬ ОТЗЫВ", f"rev:start:{deal_id}")],
        BACK, HOME,
    ])


# ---------- Кошелёк ----------

def wallet_kb() -> InlineKeyboardMarkup:
    return inl([
        [("➕ ПОПОЛНИТЬ", "dep:start")],
        [("💸 ВЫВЕСТИ", "wd:start")],
        [("📜 ИСТОРИЯ ОПЕРАЦИЙ", "hist:0")],
        [("❓ КАК ЭТО РАБОТАЕТ", "wallet:how")],
        HOME,
    ])


def deposit_amount_kb() -> InlineKeyboardMarkup:
    return inl([
        [("500 ₽", "dep:quick:500"), ("1000 ₽", "dep:quick:1000"), ("2000 ₽", "dep:quick:2000")],
        [("5000 ₽", "dep:quick:5000"), ("10000 ₽", "dep:quick:10000")],
        [("✏️ ДРУГАЯ СУММА", "dep:custom")],
        [("🔙 НАЗАД", "wallet:menu")],
        HOME,
    ])


def deposit_confirm_kb(amount: str) -> InlineKeyboardMarkup:
    return inl([
        [("✅ ОТПРАВИТЬ ЗАЯВКУ", f"dep:submit:{amount}")],
        [("✏️ ИЗМЕНИТЬ СУММУ", "dep:custom")],
        [("❌ ОТМЕНА", "wallet:menu")],
        HOME,
    ])


def withdraw_confirm_kb(amount: str, details_hash: str) -> InlineKeyboardMarkup:
    return inl([
        [("✅ СОЗДАТЬ ЗАЯВКУ", f"wd:submit:{amount}:{details_hash}")],
        [("✏️ ИЗМЕНИТЬ", "wd:restart")],
        [("❌ ОТМЕНА", "wallet:menu")],
        HOME,
    ])


HISTORY_PAGE_SIZE = 7


def history_kb(page: int, has_next: bool, has_prev: bool) -> InlineKeyboardMarkup:
    rows = []
    nav = []
    if has_prev:
        nav.append(("⬅️ РАНЬШЕ", f"hist:{page - 1}"))
    if has_next:
        nav.append((("НОВЕЕ ➡️" if has_prev else "➡️ НОВЕЕ"), f"hist:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([("🔙 КОШЕЛЁК", "wallet:menu")])
    rows.append(HOME)
    return inl(rows)


# ---------- Профиль ----------

def profile_kb() -> InlineKeyboardMarkup:
    return inl([
        [("⭐ МОИ ОТЗЫВЫ", "rev:mine")],
        [("📊 СТАТИСТИКА", "profile:stats")],
        [("⚙️ НАСТРОЙКИ", "profile:settings")],
        HOME,
    ])


def public_profile_kb(user_db_id: int) -> InlineKeyboardMarkup:
    return inl([
        [("⭐ ОТЗЫВЫ ПОЛЬЗОВАТЕЛЯ", f"rev:user:{user_db_id}")],
        BACK, HOME,
    ])


def settings_kb() -> InlineKeyboardMarkup:
    return inl([
        [("✏️ ИЗМЕНИТЬ НИКНЕЙМ", "set:nick")],
        [("🔙 НАЗАД", "profile:me")],
        HOME,
    ])


# ---------- Отзывы ----------

def stars_kb(deal_id: int) -> InlineKeyboardMarkup:
    return inl([
        [("⭐", f"rev:star:{deal_id}:1"), ("⭐⭐", f"rev:star:{deal_id}:2"),
         ("⭐⭐⭐", f"rev:star:{deal_id}:3"), ("⭐⭐⭐⭐", f"rev:star:{deal_id}:4"),
         ("⭐⭐⭐⭐⭐", f"rev:star:{deal_id}:5")],
        [("🔙 НАЗАД", f"deal:view:{deal_id}")],
        HOME,
    ])


def review_skip_kb(deal_id: int) -> InlineKeyboardMarkup:
    return inl([
        [("✅ ОТПРАВИТЬ БЕЗ ТЕКСТА", f"rev:send:{deal_id}:skip")],
        [("🔙 НАЗАД", f"deal:view:{deal_id}")],
        HOME,
    ])


# ---------- FAQ ----------

def faq_kb() -> InlineKeyboardMarkup:
    return inl([
        [("💰 КОМИССИЯ", "faq:commission")],
        [("🤝 КАК РАБОТАЮТ СДЕЛКИ", "faq:deals")],
        [("🔒 БЕЗОПАСНОСТЬ", "faq:security")],
        [("💳 ОПЛАТА И БАЛАНС", "faq:payments")],
        [("💸 ВЫВОД СРЕДСТВ", "faq:withdraw")],
        [("⚖️ СПОРЫ И ВОЗВРАТЫ", "faq:disputes")],
        [("👤 АККАУНТ", "faq:account")],
        [("📢 НОВОСТИ", "news:list")],
        [("🆘 ПОДДЕРЖКА", "support:menu")],
        HOME,
    ])


def faq_topic_kb(back_cb: str) -> InlineKeyboardMarkup:
    return inl([[(("🔙 КО ВСЕМ ТЕМАМ", back_cb))], HOME])


# ---------- Новости / поддержка ----------

def news_kb(posts: list) -> InlineKeyboardMarkup:
    rows = [[(f"📌 {p.title[:40]}", f"news:view:{p.id}")] for p in posts[:15]]
    rows.append(BACK)
    rows.append(HOME)
    return inl(rows)


def support_kb(support_url: str) -> InlineKeyboardMarkup:
    kb = inl([[("✉️ НАПИСАТЬ СООБЩЕНИЕ БOTУ", "support:write")], HOME])
    kb.inline_keyboard.insert(0, [InlineKeyboardButton(
        text=f"🆘 ОТКРЫТЬ {SUPPORT_TAG}", url=support_url)])
    return kb


SUPPORT_TAG = "@PIayerokSupports"


# ---------- Споры ----------

DISPUTE_REASONS = ["Товар не получен", "Товар не соответствует описанию",
                   "Продавец не выходит на связь", "Ошибка оплаты", "Другое"]


def dispute_reasons_kb(deal_id: int) -> InlineKeyboardMarkup:
    rows = [[(r, f"disp:reason:{deal_id}:{i}")] for i, r in enumerate(DISPUTE_REASONS)]
    rows.append([("🔙 НАЗАД", f"deal:view:{deal_id}")])
    rows.append(HOME)
    return inl(rows)


def dispute_confirm_kb(deal_id: int) -> InlineKeyboardMarkup:
    return inl([
        [("✅ ОТПРАВИТЬ СПОР", f"disp:submit:{deal_id}")],
        [("✏️ ИЗМЕНИТЬ ОПИСАНИЕ", f"disp:redesc:{deal_id}")],
        [("❌ ОТМЕНА", f"deal:view:{deal_id}")],
        HOME,
    ])
