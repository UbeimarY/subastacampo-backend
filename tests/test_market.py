from datetime import UTC, datetime

import anyio
import httpx
import pytest

from subastacampo.db.models import ProductCategory
from subastacampo.pricing.comparables import Sale
from subastacampo.pricing.market import MAX_DAYS, MIN_DAYS, period_days, summarize
from tests.conftest import FakeClock
from tests.rpc import call
from tests.test_auction_contact import closed_auction

pytestmark = pytest.mark.anyio


def sale(kg: float, per_kg: float, day: int) -> Sale:
    return Sale("Papa", kg, int(kg * per_kg), datetime(2026, 11, day, 12, tzinfo=UTC))


def test_medians_resist_one_unusual_lot_and_weeks_start_on_monday() -> None:
    sales = [sale(100, 1800, 2), sale(100, 2000, 4), sale(100, 9000, 5), sale(50, 1900, 10)]

    [tubers] = summarize({ProductCategory.TUBERS: sales, ProductCategory.FRUITS: []})

    assert tubers.median_price_per_kg == 1950
    assert (tubers.lowest_price_per_kg, tubers.highest_price_per_kg) == (1800, 9000)
    assert [(week.start.isoformat(), week.sales) for week in tubers.weeks] == [
        ("2026-11-02", 3),
        ("2026-11-09", 1),
    ]


def test_the_period_is_held_between_its_bounds() -> None:
    assert period_days(0) == 60
    assert period_days(1) == MIN_DAYS
    assert period_days(10_000) == MAX_DAYS


async def test_market_prices_are_public_and_count_only_sales(
    api: httpx.AsyncClient, producer: str, buyer: str, clock: FakeClock
) -> None:
    sold = await closed_auction(api, producer, buyer, clock)  # 1000 kg for $500.000
    for _ in range(100):
        status = (await call(api, "AuctionService", "GetAuction", {"auctionId": sold})).json()
        if status["auction"]["status"] == "AUCTION_STATUS_CLOSED":
            break
        await anyio.sleep(0.05)

    response = await call(api, "CatalogService", "GetMarketPrices", {})

    assert response.status_code == 200
    [market] = response.json()["categories"]
    assert market["category"] == "PRODUCT_CATEGORY_TUBERS"
    assert market["sales"] == 1
    assert market["medianPricePerKg"]["units"] == "500"
