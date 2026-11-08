"""Typed application settings loaded from environment variables and .env."""

from functools import lru_cache
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_ASYNC_DRIVER = "postgresql+asyncpg://"
_KNOWN_PREFIXES = ("postgres://", "postgresql://", "postgresql+psycopg://")


def normalize_database_url(value: str) -> str:
    """Adapts a URL as hosting providers hand it out to the asyncpg driver.

    - Scheme: postgres:// or postgresql:// becomes postgresql+asyncpg://
    - libpq options asyncpg does not understand: sslmode becomes ssl,
      channel_binding is removed (asyncpg negotiates it by itself).
    """
    for prefix in _KNOWN_PREFIXES:
        if value.startswith(prefix):
            value = _ASYNC_DRIVER + value.removeprefix(prefix)
            break

    parts = urlsplit(value)
    query = dict(parse_qsl(parts.query))
    sslmode = query.pop("sslmode", None)
    query.pop("channel_binding", None)
    if sslmode and "ssl" not in query:
        query["ssl"] = sslmode
    return urlunsplit(parts._replace(query=urlencode(query)))


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str
    jwt_secret: str
    jwt_expire_minutes: int = 60 * 24
    cors_origins: list[str] = ["http://localhost:4200"]
    # Business dates ("harvested today") follow the farmers' local time zone.
    business_timezone: str = "America/Bogota"

    @field_validator("database_url")
    @classmethod
    def use_async_driver(cls, value: str) -> str:
        return normalize_database_url(value)


@lru_cache
def get_settings() -> Settings:
    return Settings()
