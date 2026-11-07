from datetime import UTC, datetime, timedelta

from subastacampo.auctions.rules import apply_anti_sniping, has_expired, minimum_next_bid

NOW = datetime(2026, 11, 4, 15, 0, tzinfo=UTC)


def test_first_bid_may_match_the_starting_price() -> None:
    assert (
        minimum_next_bid(starting_price=500, current_price=500, min_increment=10, bid_count=0)
        == 500
    )


def test_later_bids_must_beat_the_current_price() -> None:
    assert (
        minimum_next_bid(starting_price=500, current_price=600, min_increment=10, bid_count=3)
        == 610
    )


def test_bid_in_the_last_minutes_extends_the_close() -> None:
    result = apply_anti_sniping(now=NOW, ends_at=NOW + timedelta(seconds=30))

    assert result.extended
    assert result.ends_at == NOW + timedelta(minutes=2)


def test_early_bid_does_not_extend_the_close() -> None:
    ends_at = NOW + timedelta(minutes=10)

    result = apply_anti_sniping(now=NOW, ends_at=ends_at)

    assert not result.extended
    assert result.ends_at == ends_at


def test_auction_expires_exactly_at_its_end() -> None:
    assert has_expired(now=NOW, ends_at=NOW)
    assert not has_expired(now=NOW, ends_at=NOW + timedelta(seconds=1))
