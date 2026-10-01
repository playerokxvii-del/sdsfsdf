"""Состояния FSM."""
from aiogram.fsm.state import State, StatesGroup


class Registration(StatesGroup):
    phone = State()
    nickname = State()
    subscribe = State()


class DealCreate(StatesGroup):
    category = State()
    game = State()          # выбор игры из списка / "другая"
    game_custom = State()   # ввод своей игры
    subcategory = State()
    title = State()
    link = State()
    price = State()
    description = State()
    preview = State()


class DepositStates(StatesGroup):
    amount = State()
    confirm = State()


class WithdrawStates(StatesGroup):
    amount = State()
    details = State()
    confirm = State()


class DisputeStates(StatesGroup):
    reason = State()
    description = State()
    confirm = State()


class SupportStates(StatesGroup):
    message = State()


class ReviewStates(StatesGroup):
    stars = State()
    text = State()
    confirm = State()


class SettingsStates(StatesGroup):
    change_nick = State()


class AdminStates(StatesGroup):
    # категории
    cat_add_name = State()
    cat_rename = State()
    game_add = State()
    subcat_add = State()
    # настройки
    set_commission = State()
    set_requisites = State()
    set_recipient = State()
    set_min_deposit = State()
    set_min_withdraw = State()
    set_channel = State()
    set_support = State()
    # баннеры
    banner_section = State()
    banner_file = State()
    # новости
    news_title = State()
    news_text = State()
    # рассылка
    bc_text = State()
    bc_banner = State()
    bc_btn1_text = State()
    bc_btn1_url = State()
    bc_btn2_text = State()
    bc_btn2_url = State()
    bc_audience = State()
    bc_preview = State()
    # пользователи
    user_search = State()
    user_adjust_amount = State()
    # модерация/платежи заметки
    mod_note = State()
    pay_note = State()
    wdr_note = State()
    disp_seller_note = State()
    disp_buyer_note = State()
