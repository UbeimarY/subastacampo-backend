"""The live market feed: a first answer at once, then one per closed auction."""

import asyncio
import uuid
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from subastacampo.auctions.broker import AuctionEventBroker
from subastacampo.config import Settings
from subastacampo.pricing.model import PricingModel
from subastacampo.services.catalog import CatalogServiceImpl
from subastacampo.v1.auction_pb2 import AuctionClosed, BidPlaced, WatchAuctionResponse
from subastacampo.v1.catalog_pb2 import WatchMarketPricesRequest
from tests.conftest import TODAY, FixedCalendar

pytestmark = pytest.mark.anyio


@pytest.fixture
async def service(database_settings: Settings) -> Any:
    engine = create_async_engine(database_settings.database_url)
    broker = AuctionEventBroker()
    yield (
        CatalogServiceImpl(
            async_sessionmaker(engine, expire_on_commit=False),
            FixedCalendar(TODAY),
            PricingModel.load(),
            broker,
            market_refresh_seconds=30,
        ),
        broker,
    )
    await engine.dispose()


async def test_a_closed_auction_pushes_fresh_prices(service: Any) -> None:
    catalog, broker = service
    stream = catalog.watch_market_prices(WatchMarketPricesRequest(days=30), None)

    first = await anext(stream)
    assert first.days == 30
    # A bid does not change market prices; a close does.
    broker.publish(uuid.uuid4(), WatchAuctionResponse(bid_placed=BidPlaced()))
    broker.publish(uuid.uuid4(), WatchAuctionResponse(auction_closed=AuctionClosed()))

    second = await asyncio.wait_for(anext(stream), timeout=2)

    assert second.updated_at.seconds >= first.updated_at.seconds
    await stream.aclose()


async def test_without_closes_the_feed_still_refreshes(service: Any) -> None:
    catalog, _broker = service
    catalog._market_refresh_seconds = 0.05
    stream = catalog.watch_market_prices(WatchMarketPricesRequest(), None)

    await anext(stream)
    refreshed = await asyncio.wait_for(anext(stream), timeout=2)

    assert refreshed.days == 60
    await stream.aclose()


async def test_bids_alone_do_not_wake_the_market_feed(service: Any) -> None:
    catalog, broker = service
    stream = catalog.watch_market_prices(WatchMarketPricesRequest(), None)
    await anext(stream)

    broker.publish(uuid.uuid4(), WatchAuctionResponse(bid_placed=BidPlaced()))

    with pytest.raises(TimeoutError):
        await asyncio.wait_for(anext(stream), timeout=0.3)


async def test_a_burst_of_closes_sends_a_single_update(service: Any) -> None:
    catalog, broker = service
    stream = catalog.watch_market_prices(WatchMarketPricesRequest(), None)
    await anext(stream)

    for _ in range(5):
        broker.publish(uuid.uuid4(), WatchAuctionResponse(auction_closed=AuctionClosed()))
    await asyncio.wait_for(anext(stream), timeout=2)

    with pytest.raises(TimeoutError):
        await asyncio.wait_for(anext(stream), timeout=0.3)
