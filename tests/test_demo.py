import secrets
from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from subastacampo.config import Settings
from subastacampo.db.models import Auction, AuctionStatus, AutoBid, Bid
from subastacampo.demo import Dice, seed

pytestmark = pytest.mark.anyio


async def test_the_demo_fills_history_and_live_auctions_that_follow_the_rules(
    database_settings: Settings,
) -> None:
    engine = create_async_engine(database_settings.database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as session:
        counts = await seed(session, secrets.token_urlsafe(12), datetime.now(UTC))
        await session.commit()

        live = await session.scalar(
            select(func.count()).where(Auction.status == AuctionStatus.ACTIVE)
        )
        assert live == counts["live"] == 8
        assert await session.scalar(select(func.count()).select_from(AutoBid)) == 1
        # Every auction's price is its highest bid, never below the starting price.
        for auction in (await session.scalars(select(Auction))).all():
            highest = await session.scalar(
                select(func.max(Bid.amount)).where(Bid.auction_id == auction.id)
            )
            assert auction.current_price == (highest or auction.starting_price)
            assert (auction.winner_id is not None) == (
                auction.status == AuctionStatus.CLOSED and highest is not None
            )
    await engine.dispose()


def test_the_dice_stay_within_their_bounds() -> None:
    dice = Dice()
    rolls = [dice.randint(2, 4) for _ in range(200)]
    spread = [dice.uniform(0.8, 1.05) for _ in range(200)]

    assert set(rolls) == {2, 3, 4}
    assert all(0.8 <= value <= 1.05 for value in spread)
    assert sorted(dice.sample(range(10), k=10)) == list(range(10))
    assert len(set(dice.sample("abcdef", k=4))) == 4
