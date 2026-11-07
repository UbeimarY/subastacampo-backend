"""Closing auctions whose time is up and picking the winner."""

import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from subastacampo.auctions.rules import has_expired
from subastacampo.db.models import Auction, AuctionStatus, Bid


async def load_auction(
    session: AsyncSession, auction_id: uuid.UUID, *, for_update: bool = False
) -> Auction | None:
    """Loads an auction with its product.

    With for_update, the row stays locked until the transaction ends, so
    concurrent bids are processed one at a time. selectinload fetches the
    product in a separate query, so only the auction row gets locked.
    """
    query = select(Auction).where(Auction.id == auction_id).options(selectinload(Auction.product))
    if for_update:
        query = query.with_for_update().execution_options(populate_existing=True)
    return await session.scalar(query)


async def close_if_due(session: AsyncSession, auction: Auction, now: datetime) -> bool:
    """Closes an expired auction and sets the winner. The caller must hold the row lock."""
    if auction.status != AuctionStatus.ACTIVE or not has_expired(now=now, ends_at=auction.ends_at):
        return False

    # Highest amount wins; on a tie (impossible with increments, but safe), the earliest.
    auction.winner_id = await session.scalar(
        select(Bid.bidder_id)
        .where(Bid.auction_id == auction.id)
        .order_by(Bid.amount.desc(), Bid.placed_at.asc())
        .limit(1)
    )
    auction.status = AuctionStatus.CLOSED
    return True
