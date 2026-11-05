from collections.abc import AsyncIterator

import httpx
import pytest

from subastacampo.app import create_app
from subastacampo.config import Settings
from tests.conftest import TEST_JWT_SECRET

pytestmark = pytest.mark.anyio

# Nothing listens on port 1, so every connection attempt fails fast.
UNREACHABLE_DATABASE = "postgresql://nobody:nothing@127.0.0.1:1/missing"
STATUS_PATH = "/subastacampo.v1.SystemService/GetStatus"


@pytest.fixture
def settings() -> Settings:
    return Settings(database_url=UNREACHABLE_DATABASE, jwt_secret=TEST_JWT_SECRET)


@pytest.fixture
async def client(settings: Settings) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=create_app(settings))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


async def test_health_endpoint_returns_ok(client: httpx.AsyncClient) -> None:
    response = await client.get("/healthz")

    assert response.status_code == 200
    assert response.text == "ok"


async def test_unknown_service_returns_404(client: httpx.AsyncClient) -> None:
    response = await client.post("/subastacampo.v1.UnknownService/Do", json={})

    assert response.status_code == 404


async def test_get_status_reports_database_down(client: httpx.AsyncClient) -> None:
    response = await client.post(STATUS_PATH, json={}, headers={"Connect-Protocol-Version": "1"})

    assert response.status_code == 200
    body = response.json()
    assert body["version"]
    assert "serverTime" in body
    # Proto3 JSON omits fields holding their default value, so false is absent.
    assert body.get("databaseOk", False) is False


def test_settings_normalize_database_driver() -> None:
    settings = Settings(database_url="postgres://user:pass@host:5432/db", jwt_secret="x")

    assert settings.database_url == "postgresql+asyncpg://user:pass@host:5432/db"
