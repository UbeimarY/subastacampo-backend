import asyncio
import uuid
from datetime import timedelta

import httpx
import pytest

from subastacampo.auctions.broker import AuctionEventBroker
from tests.conftest import FakeClock
from tests.rpc import stream_call, wait_until
from tests.test_auctions import STARTING_PRICE, bid, start_auction

pytestmark = pytest.mark.anyio


async def test_watchers_receive_bids_and_the_close_live(
    api: httpx.AsyncClient,
    producer: str,
    buyer: str,
    clock: FakeClock,
    broker: AuctionEventBroker,
) -> None:
    auction = await start_auction(api, producer, minutes=10)
    auction_id = auction["id"]

    watch = asyncio.create_task(
        stream_call(api, "AuctionService", "WatchAuction", {"auctionId": auction_id})
    )
    await wait_until(lambda: broker.subscriber_count(uuid.UUID(auction_id)) == 1)

    await bid(api, buyer, auction_id, STARTING_PRICE)
    clock.advance(timedelta(minutes=11))  # the background closer notices it

    messages = await asyncio.wait_for(watch, timeout=5)

    assert [next(iter(message)) for message in messages] == [
        "snapshot",
        "bidPlaced",
        "auctionClosed",
    ]
    placed = messages[1]["bidPlaced"]
    assert placed["auction"]["currentPrice"]["units"] == str(STARTING_PRICE)
    # An anonymous watcher never sees the risk of a bid.
    assert "risk" not in placed["bid"]
    assert messages[2]["auctionClosed"]["auction"]["status"] == "AUCTION_STATUS_CLOSED"


async def test_watching_a_finished_auction_sends_its_final_state(
    api: httpx.AsyncClient, producer: str, clock: FakeClock
) -> None:
    auction = await start_auction(api, producer, minutes=10)
    clock.advance(timedelta(minutes=11))

    messages = await stream_call(
        api, "AuctionService", "WatchAuction", {"auctionId": auction["id"]}
    )

    assert len(messages) == 1
    assert messages[0]["snapshot"]["auction"]["status"] == "AUCTION_STATUS_CLOSED"
