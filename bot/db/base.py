"""Асинхронный слой БД: движок, сессии, инициализация, seed данных."""
from __future__ import annotations

from decimal import Decimal

from sqlalchemy.ext.asyncio import (
    AsyncSession, async_sessionmaker, create_async_engine,
)
from sqlalchemy.pool import NullPool

from bot.config.settings import get_config
from bot.db.models import Base, Category, Game, Setting

_engine = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def get_engine():
    global _engine
    if _engine is None:
        cfg = get_config()
        kwargs = {}
        if cfg.DATABASE_URL.startswith("sqlite"):
            kwargs["connect_args"] = {"check_same_thread": False}
        else:
            kwargs["poolclass"] = NullPool
        _engine = create_async_engine(cfg.DATABASE_URL, echo=False, **kwargs)
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(get_engine(), expire_on_commit=False)
    return _session_factory


async def get_db() -> AsyncSession:
    """Dependency для роутеров/обработчиков."""
    async with get_session_factory()() as session:
        yield session


DEFAULT_CATEGORIES = [
    ("ИГРЫ", "🎮", True),
    ("ИГРОВАЯ ВАЛЮТА", "💎", True),
    ("ИГРОВЫЕ ПРЕДМЕТЫ", "🎁", True),
    ("СКИНЫ", "🎨", True),
    ("АККАУНТЫ", "👤", True),
    ("КЛЮЧИ", "🔑", False),
    ("ЦИФРОВЫЕ ИГРЫ", "🕹️", False),
    ("ПОПОЛНЕНИЕ БАЛАНСА", "💳", False),
    ("ПОДПИСКИ", "⭐", False),
    ("УСЛУГИ", "🛠️", False),
    ("БУСТ", "🚀", True),
    ("TELEGRAM", "📱", False),
    ("NFT / ЦИФРОВЫЕ ПОДАРКИ", "🎁", False),
    ("ДИЗАЙН", "🎨", False),
    ("ОБУЧЕНИЕ", "📚", False),
    ("МЕДИА", "📺", False),
    ("АРЕНДА", "🏠", False),
    ("МОДЫ", "🧩", True),
    ("ДРУГОЕ", "📦", False),
]

DEFAULT_GAMES = [
    "Roblox", "Brawl Stars", "PUBG Mobile", "Standoff 2", "Fortnite",
    "Minecraft", "Genshin Impact", "Steam", "PlayStation", "Mobile Legends",
    "Clash of Clans", "Valorant",
]

GAME_CATEGORIES = {"ИГРЫ", "ИГРОВАЯ ВАЛЮТА", "ИГРОВЫЕ ПРЕДМЕТЫ", "СКИНЫ",
                   "АККАУНТЫ", "БУСТ", "МОДЫ"}

DEFAULT_SETTINGS = {
    "commission_percent": "10",
    "min_deposit": "50",
    "min_withdraw": "100",
    "payment_requisites": (
        "💳 РЕКВИЗИТЫ ДЛЯ ПОПОЛНЕНИЯ\n\n"
        "🏦 Банк: Т-Банк\n"
        "💳 Карта: 0000 0000 0000 0000\n"
        "👤 Получатель: Пример И. И.\n\n"
        "❗ После перевода отправьте скриншот в поддержку @PIayerokSupports."
    ),
    "withdraw_details_hint": (
        "Отправьте реквизиты одной строкой, например:\n"
        "2202 2000 0000 0000 | Банк | Получатель"
    ),
    "maintenance": "0",
    "channel_link": "https://t.me/Playerok",
    "support_link": "https://t.me/PIayerokSupports",
}


async def init_db() -> None:
    """Создание таблиц + сидирование при первом запуске."""
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with get_session_factory()() as s:
        # настройки
        for key, value in DEFAULT_SETTINGS.items():
            exists = await s.get(Setting, key)
            if exists is None:
                s.add(Setting(key=key, value=value))
        # категории
        from sqlalchemy import select
        cnt = (await s.execute(select(Category).limit(1))).scalar_one_or_none()
        if cnt is None:
            for i, (name, emoji, req_game) in enumerate(DEFAULT_CATEGORIES):
                cat = Category(name=name, emoji=emoji, sort_order=i,
                               active=True, requires_game=req_game)
                s.add(cat)
                if name in GAME_CATEGORIES:
                    for j, gname in enumerate(DEFAULT_GAMES):
                        s.add(Game(category_id=cat.id if cat.id else 0,
                                   name=gname, sort_order=j))
        await s.commit()

    # игры привязываются после flush — делаем отдельным проходом корректно
    async with get_session_factory()() as s:
        from sqlalchemy import select
        cats = (await s.execute(
            select(Category).where(Category.requires_game.is_(True))
        )).scalars().all()
        for cat in cats:
            existing = (await s.execute(
                select(Game).where(Game.category_id == cat.id).limit(1)
            )).scalar_one_or_none()
            if existing is None:
                for j, gname in enumerate(DEFAULT_GAMES):
                    s.add(Game(category_id=cat.id, name=gname, sort_order=j))
        await s.commit()


async def get_setting(session: AsyncSession, key: str, default: str = "") -> str:
    st = await session.get(Setting, key)
    return st.value if st else default


async def set_setting(session: AsyncSession, key: str, value: str) -> None:
    st = await session.get(Setting, key)
    if st is None:
        session.add(Setting(key=key, value=value))
    else:
        st.value = value
    await session.commit()


async def get_commission_percent(session: AsyncSession) -> Decimal:
    raw = await get_setting(session, "commission_percent", "10")
    try:
        p = Decimal(raw)
    except Exception:
        p = Decimal("10")
    if p < 0 or p > 90:
        p = Decimal("10")
    return p
