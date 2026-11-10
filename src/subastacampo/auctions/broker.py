"""In-process publish/subscribe for live auction events.

Subscribers live in this server's memory. With several server instances, the same
interface would be backed by PostgreSQL LISTEN/NOTIFY or Redis instead.
"""

import asyncio
import contextlib
import uuid
from collections import defaultdict
from collections.abc import Iterator

from subastacampo.v1.auction_pb2 import WatchAuctionResponse

DEFAULT_QUEUE_SIZE = 100
# Channel of the market-wide feed: it hears every auction that closes.
MARKET_CHANNEL = uuid.UUID(int=0)


class SubscriptionOverflowError(Exception):
    """The subscriber fell too far behind and was dropped. It must reconnect."""


class Subscription:
    def __init__(self, queue_size: int) -> None:
        self._queue: asyncio.Queue[WatchAuctionResponse | None] = asyncio.Queue(queue_size)

    def offer(self, event: WatchAuctionResponse) -> bool:
        try:
            self._queue.put_nowait(event)
        except asyncio.QueueFull:
            return False
        return True

    def abort(self) -> None:
        """Discards pending events and wakes the consumer with an end marker."""
        while not self._queue.empty():
            self._queue.get_nowait()
        self._queue.put_nowait(None)

    def drain(self) -> int:
        """Drops the events already waiting; returns how many. An end marker stays."""
        dropped = 0
        while not self._queue.empty():
            event = self._queue.get_nowait()
            if event is None:
                self._queue.put_nowait(None)
                break
            dropped += 1
        return dropped

    async def next(self) -> WatchAuctionResponse:
        event = await self._queue.get()
        if event is None:
            raise SubscriptionOverflowError
        return event


class AuctionEventBroker:
    def __init__(self, queue_size: int = DEFAULT_QUEUE_SIZE) -> None:
        self._queue_size = queue_size
        self._subscriptions: dict[uuid.UUID, set[Subscription]] = defaultdict(set)

    @contextlib.contextmanager
    def subscribe(self, auction_id: uuid.UUID) -> Iterator[Subscription]:
        subscription = Subscription(self._queue_size)
        self._subscriptions[auction_id].add(subscription)
        try:
            yield subscription
        finally:
            self._remove(auction_id, subscription)

    def publish(self, auction_id: uuid.UUID, event: WatchAuctionResponse) -> None:
        self._deliver(auction_id, event)
        # A close changes the market prices: the market feed hears it too.
        if event.WhichOneof("event") == "auction_closed" and auction_id != MARKET_CHANNEL:
            self._deliver(MARKET_CHANNEL, event)

    def subscribe_market(self) -> contextlib.AbstractContextManager[Subscription]:
        """Every auction close, from any auction."""
        return self.subscribe(MARKET_CHANNEL)

    def _deliver(self, auction_id: uuid.UUID, event: WatchAuctionResponse) -> None:
        for subscription in list(self._subscriptions.get(auction_id, ())):
            if not subscription.offer(event):
                # A slow client must never slow down the others: drop it.
                # It reconnects and receives a fresh snapshot, so nothing is lost.
                self._remove(auction_id, subscription)
                subscription.abort()

    def subscriber_count(self, auction_id: uuid.UUID) -> int:
        return len(self._subscriptions.get(auction_id, ()))

    def _remove(self, auction_id: uuid.UUID, subscription: Subscription) -> None:
        subscriptions = self._subscriptions.get(auction_id)
        if subscriptions is None:
            return
        subscriptions.discard(subscription)
        if not subscriptions:
            del self._subscriptions[auction_id]
