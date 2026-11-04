"""Typed application settings loaded from environment variables and .env."""

from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_ASYNC_DRIVER = "postgresql+asyncpg://"
_KNOWN_PREFIXES = ("postgres://", "postgresql://", "postgresql+psycopg://")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str
    jwt_secret: str
    jwt_expire_minutes: int = 60 * 24
    cors_origins: list[str] = ["http://localhost:4200"]

    @field_validator("database_url")
    @classmethod
    def use_async_driver(cls, value: str) -> str:
        """Hosting platforms hand out plain postgres:// URLs; we need the asyncpg driver."""
        for prefix in _KNOWN_PREFIXES:
            if value.startswith(prefix):
                return _ASYNC_DRIVER + value.removeprefix(prefix)
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
