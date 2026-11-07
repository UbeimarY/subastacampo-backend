"""Background task that closes expired auctions on time and announces it."""

import asyncio
import contextlib
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from subastacampo.auctions.broker import AuctionEventBroker
from subastacampo.auctions.closing import close_if_due
from subastacampo.auctions.events import auction_closed_event
from subastacampo.calendar import BusinessCalendar
from subastacampo.clock import Clock
from subastacampo.db.models import Auction, AuctionStatus

logger = logging.getLogger(__name__)


class AuctionCloser:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        broker: AuctionEventBroker,
        clock: Clock,
        calendar: BusinessCalendar,
        interval_seconds: float,
    ) -> None:
        self._session_factory = session_factory
        self._broker = broker
        self._clock = clock
        self._calendar = calendar
        self._interval = interval_seconds
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name="auction-closer")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task

    async def close_due(self) -> int:
        now = self._clock.now()
        async with self._session_factory() as session:
            due = (
                await session.scalars(
                    select(Auction)
                    .where(Auction.status == AuctionStatus.ACTIVE, Auction.ends_at <= now)
                    .options(selectinload(Auction.product))
                    # A bid in progress holds the lock: skip it, retry next tick.
                    .with_for_update(skip_locked=True)
                )
            ).all()
            closed = [auction for auction in due if await close_if_due(session, auction, now)]
            await session.commit()

        today = self._calendar.today()
        for auction in closed:
            self._broker.publish(auction.id, auction_closed_event(auction, today=today))
        return len(closed)

    async def _run(self) -> None:
        while True:
            try:
                await self.close_due()
            except Exception:
                # A transient failure (e.g. a database restart) must not kill the loop.
                logger.exception("Closing expired auctions failed")
            await asyncio.sleep(self._interval)
            