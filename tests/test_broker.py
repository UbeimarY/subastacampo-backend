import uuid

import pytest

from subastacampo.auctions.broker import AuctionEventBroker, SubscriptionOverflowError
from subastacampo.v1.auction_pb2 import WatchAuctionResponse

pytestmark = pytest.mark.anyio

AUCTION = uuid.uuid4()


async def test_published_events_reach_every_subscriber() -> None:
    broker = AuctionEventBroker()
    event = WatchAuctionResponse()

    with broker.subscribe(AUCTION) as first, broker.subscribe(AUCTION) as second:
        broker.publish(AUCTION, event)

        assert await first.next() is event
        assert await second.next() is event


async def test_a_slow_subscriber_is_dropped_without_blocking_the_others() -> None:
    broker = AuctionEventBroker(queue_size=1)

    with broker.subscribe(AUCTION) as slow, broker.subscribe(AUCTION) as fast:
        broker.publish(AUCTION, WatchAuctionResponse())
        await fast.next()
        broker.publish(AUCTION, WatchAuctionResponse())

        assert broker.subscriber_count(AUCTION) == 1
        with pytest.raises(SubscriptionOverflowError):
            await slow.next()
        await fast.next()


def test_leaving_the_stream_releases_the_subscription() -> None:
    broker = AuctionEventBroker()

    with broker.subscribe(AUCTION):
        assert broker.subscriber_count(AUCTION) == 1

    assert broker.subscriber_count(AUCTION) == 0
