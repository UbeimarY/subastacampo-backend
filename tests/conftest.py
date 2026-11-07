from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, timedelta

import httpx
import pytest
from asgi_lifespan import LifespanManager
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.ext.asyncio import create_async_engine

from subastacampo.app import create_app
from subastacampo.auctions.broker import AuctionEventBroker
from subastacampo.calendar import BusinessCalendar
from subastacampo.config import Settings
from subastacampo.db import models  # noqa: F401  (registers every table)
from subastacampo.db.base import Base

TEST_JWT_SECRET = "test-secret-with-at-least-32-characters"

# Every test sees the same business date, wherever and whenever it runs.
TODAY = date(2026, 11, 4)
NOW = datetime(2026, 11, 4, 15, 0, tzinfo=UTC)


class FixedCalendar(BusinessCalendar):
    def __init__(self, today: date) -> None:
        super().__init__("America/Bogota")
        self._today = today

    def today(self) -> date:
        return self._today


class FakeClock:
    """Time only moves when a test says so."""

    def __init__(self, start: datetime) -> None:
        self._now = start

    def now(self) -> datetime:
        return self._now

    def advance(self, delta: timedelta) -> None:
        self._now += delta


class _TestEnvironment(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    test_database_url: str


@pytest.fixture
def anyio_backend() -> str:
    """Run async tests on asyncio (the event loop Uvicorn uses)."""
    return "asyncio"


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(NOW)


@pytest.fixture
def broker() -> AuctionEventBroker:
    return AuctionEventBroker()


@pytest.fixture
async def database_settings() -> AsyncIterator[Settings]:
    """Settings pointing at a test database with a fresh, empty schema."""
    settings = Settings(
        database_url=_TestEnvironment().test_database_url, jwt_secret=TEST_JWT_SECRET
    )
    engine = create_async_engine(settings.database_url)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)
    await engine.dispose()
    yield settings


@pytest.fixture
async def api(
    database_settings: Settings, clock: FakeClock, broker: AuctionEventBroker
) -> AsyncIterator[httpx.AsyncClient]:
    """HTTP client running the real app, including startup and shutdown."""
    app = create_app(
        database_settings,
        calendar=FixedCalendar(TODAY),
        clock=clock,
        broker=broker,
        # Fast ticks so closing tests do not wait a full second.
        closer_interval_seconds=0.05,
    )
    async with LifespanManager(app) as manager:
        transport = httpx.ASGITransport(app=manager.app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
            headers={"Connect-Protocol-Version": "1"},
        ) as client:
            yield client
