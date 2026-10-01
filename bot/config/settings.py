"""Загрузка конфигурации из переменных окружения. Секреты не хардкодятся."""
from functools import lru_cache

from pydantic_settings import BaseSettings


class Config(BaseSettings):
    BOT_TOKEN: str = ""
    OWNER_ID: int = 6930148555
    DATABASE_URL: str = "sqlite+aiosqlite:///playerok.db"
    REDIS_URL: str = ""  # опционально: если пусто — используется MemoryStorage
    BOT_USERNAME: str = ""  # для deep link'ов; если пусто — берётся из getMe

    class Config:
        env_file = ".env"
        extra = "ignore"


@lru_cache
def get_config() -> Config:
    return Config()
