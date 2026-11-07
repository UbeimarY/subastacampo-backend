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
