"""Automatic bidding ("puja automática"): bids the system places for a buyer.

A buyer sets the most they will pay; the system then bids for them only what it
takes to lead, one increment at a time, never above that maximum. Maxima are
private: other bidders only ever see the resulting bids.

Resolution is a pure function over the auction's state, so the whole exchange
between several automatic bidders is decided in one step, inside the same
transaction that holds the auction's row lock.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime

from subastacampo.auctions.rules import minimum_next_bid

# Every round adds at most two bids; real exchanges end in one or two rounds.
MAX_ROUNDS = 50


@dataclass(frozen=True, slots=True)
class AutoBidLimit:
    bidder_id: uuid.UUID
    max_amount: int
    # Earlier wins a tie between equal maxima, as with manual bids.
    created_at: datetime


@dataclass(frozen=True, slots=True)
class AuctionPrice:
    starting_price: int
    min_increment: int
    current_price: int
    bid_count: int
    leader_id: uuid.UUID | None


@dataclass(frozen=True, slots=True)
class PlannedBid:
    bidder_id: uuid.UUID
    amount: int


def resolve(state: AuctionPrice, limits: list[AutoBidLimit]) -> list[PlannedBid]:
    """Bids to place, in order, until no automatic bidder can take the lead.

    The winner is the highest maximum (the earliest on a tie). It ends one
    increment above the runner-up's maximum, or at its own maximum if that is
    lower: the second-price rule of proxy bidding.
    """
    by_bidder = {limit.bidder_id: limit for limit in limits}
    planned: list[PlannedBid] = []
    leader, price, count = state.leader_id, state.current_price, state.bid_count
    beaten: set[uuid.UUID] = set()

    def next_minimum(current: int, bids: int) -> int:
        return minimum_next_bid(
            starting_price=state.starting_price,
            current_price=current,
            min_increment=state.min_increment,
            bid_count=bids,
        )

    def place(bidder: uuid.UUID, amount: int) -> None:
        nonlocal leader, price, count
        planned.append(PlannedBid(bidder, amount))
        leader, price, count = bidder, amount, count + 1

    for _ in range(MAX_ROUNDS):
        minimum = next_minimum(price, count)
        challengers = sorted(
            (
                limit
                for limit in limits
                if limit.bidder_id != leader
                and limit.bidder_id not in beaten
                and limit.max_amount >= minimum
            ),
            key=lambda limit: (-limit.max_amount, limit.created_at),
        )
        if not challengers:
            break
        challenger = challengers[0]
        defender = by_bidder.get(leader) if leader is not None else None

        if defender is None or _beats(challenger, defender):
            # Lead by one increment over the runner-up's maximum (the defender or the
            # next challenger, whichever could pay more), capped at its own maximum.
            rivals = [limit.max_amount for limit in challengers[1:2]]
            if defender is not None:
                rivals.append(defender.max_amount)
            target = next_minimum(max(rivals), count) if rivals else minimum
            place(challenger.bidder_id, max(minimum, min(challenger.max_amount, target)))
            continue

        # The defender holds: the challenger's maximum is spent, and the defender
        # answers one increment above it, or matches it when that is all it has.
        holder = defender.bidder_id
        answer = challenger.max_amount + state.min_increment
        if defender.max_amount >= answer:
            place(challenger.bidder_id, challenger.max_amount)
            place(holder, answer)
        else:
            place(holder, challenger.max_amount)
        beaten.add(challenger.bidder_id)

    return planned


def _beats(challenger: AutoBidLimit, defender: AutoBidLimit) -> bool:
    if challenger.max_amount != defender.max_amount:
        return challenger.max_amount > defender.max_amount
    return challenger.created_at < defender.created_at
