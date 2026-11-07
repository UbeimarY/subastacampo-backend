"""Collects the facts the risk score needs, with a single aggregate query."""

from datetime import datetime

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from subastacampo.db.models import Auction, Bid, User
from subastacampo.risk.scoring import BidFacts

PHONE_DIGITS = 10


def same_phone(first: str | None, second: str | None) -> bool:
    """Compares the last 10 digits, so "+57 313 522 0484" equals "3135220484"."""

    def digits(value: str | None) -> str:
        return "".join(char for char in value or "" if char.isdigit())[-PHONE_DIGITS:]

    first_digits = digits(first)
    return bool(first_digits) and first_digits == digits(second)


async def gather_facts(
    session: AsyncSession,
    *,
    auction: Auction,
    bidder: User,
    now: datetime,
    extends_close: bool,
) -> BidFacts:
    """Must run before the new bid is inserted: counts exclude it."""
    in_auction = Bid.auction_id == auction.id
    counts = (
        await session.execute(
            select(
                func.count().filter(in_auction),
                func.count().filter(and_(in_auction, Bid.extended_close)),
                func.count(),
                func.count().filter(Bid.extended_close),
            ).where(Bid.bidder_id == bidder.id)
        )
    ).one()
    producer_phone = await session.scalar(
        select(User.phone).where(User.id == auction.product.producer_id)
    )

    return BidFacts(
        seconds_remaining=(auction.ends_at - now).total_seconds(),
        extends_close=extends_close,
        previous_bids_in_auction=counts[0],
        previous_extensions_in_auction=counts[1],
        historical_bids=counts[2],
        historical_last_minute_bids=counts[3],
        account_age=now - bidder.created_at,
        shares_phone_with_producer=same_phone(bidder.phone, producer_phone),
    )

