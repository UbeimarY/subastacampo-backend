"""Automatic bidding decided as a pure function: every exchange is predictable."""

import uuid
from datetime import UTC, datetime, timedelta

from subastacampo.auctions.proxy import AuctionPrice, AutoBidLimit, PlannedBid, resolve

ANA, BETO, CARO = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
T0 = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)


def limit(bidder: uuid.UUID, max_amount: int, minutes: int = 0) -> AutoBidLimit:
    return AutoBidLimit(bidder, max_amount, T0 + timedelta(minutes=minutes))


def state(price: int = 100_000, count: int = 1, leader: uuid.UUID | None = None) -> AuctionPrice:
    return AuctionPrice(
        starting_price=100_000,
        min_increment=10_000,
        current_price=price,
        bid_count=count,
        leader_id=leader,
    )


def test_an_automatic_bidder_answers_a_manual_bid_with_one_increment() -> None:
    plan = resolve(state(leader=BETO), [limit(ANA, 300_000)])

    assert plan == [PlannedBid(ANA, 110_000)]


def test_the_first_automatic_bid_opens_at_the_starting_price() -> None:
    plan = resolve(state(price=100_000, count=0), [limit(ANA, 300_000)])

    assert plan == [PlannedBid(ANA, 100_000)]


def test_two_automatic_bidders_end_one_increment_above_the_runner_up() -> None:
    plan = resolve(state(leader=BETO), [limit(ANA, 300_000), limit(BETO, 200_000, minutes=1)])

    # Ana's maximum is higher: she leads at Beto's maximum plus one increment.
    assert plan[-1] == PlannedBid(ANA, 210_000)
    assert len(plan) == 1


def test_the_leader_defends_and_the_challengers_maximum_is_spent() -> None:
    plan = resolve(state(leader=ANA), [limit(ANA, 300_000), limit(BETO, 200_000, minutes=1)])

    assert plan == [PlannedBid(BETO, 200_000), PlannedBid(ANA, 210_000)]


def test_a_leader_within_one_increment_matches_instead_of_losing_the_lead() -> None:
    plan = resolve(state(leader=ANA), [limit(ANA, 205_000), limit(BETO, 200_000, minutes=1)])

    assert plan == [PlannedBid(ANA, 200_000)]


def test_on_equal_maxima_the_earliest_wins() -> None:
    plan = resolve(state(leader=CARO), [limit(BETO, 250_000, minutes=2), limit(ANA, 250_000)])

    assert plan[-1].bidder_id == ANA
    assert all(bid.amount <= 250_000 for bid in plan)


def test_a_maximum_below_the_next_minimum_places_nothing() -> None:
    assert resolve(state(price=200_000, leader=BETO), [limit(ANA, 205_000)]) == []


def test_nobody_ever_bids_above_their_maximum_or_below_the_rules() -> None:
    limits = [limit(ANA, 500_000), limit(BETO, 480_000, 1), limit(CARO, 470_000, 2)]
    plan = resolve(state(leader=CARO), limits)

    maxima = {item.bidder_id: item.max_amount for item in limits}
    price = 100_000
    for bid in plan:
        assert bid.amount <= maxima[bid.bidder_id]
        assert bid.amount >= price + 10_000 or bid.amount == price
        price = bid.amount
    assert plan[-1] == PlannedBid(ANA, 490_000)
