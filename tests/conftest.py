import pytest


@pytest.fixture
def anyio_backend() -> str:
    """Run async tests on asyncio (the event loop Uvicorn uses)."""
    return "asyncio"
