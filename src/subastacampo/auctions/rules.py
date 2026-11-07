"""Auction rules as pure functions: the single source of truth, trivial to test."""

from dataclasses import dataclass
from datetime import datetime, timedelta

ANTI_SNIPING_WINDOW = timedelta(minutes=2)
MIN_DURATION = timedelta(minutes=1)
MAX_DURATION = timedelta(hours=24)


def minimum_next_bid(
    *, starting_price: int, current_price: int, min_increment: int, bid_count: int
) -> int:
    """The first bid may match the starting price; later bids must beat the current one."""
    return starting_price if bid_count == 0 else current_price + min_increment


@dataclass(frozen=True, slots=True)
class CloseTime:
    extended: bool
    ends_at: datetime


def apply_anti_sniping(*, now: datetime, ends_at: datetime) -> CloseTime:
    """A bid in the last minutes pushes the close, so everyone can still react."""
    if ends_at - now <= ANTI_SNIPING_WINDOW:
        return CloseTime(extended=True, ends_at=max(ends_at, now + ANTI_SNIPING_WINDOW))
    return CloseTime(extended=False, ends_at=ends_at)


def has_expired(*, now: datetime, ends_at: datetime) -> bool:
    return now >= ends_at
