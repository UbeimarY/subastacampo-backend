from dataclasses import replace
from datetime import timedelta

from subastacampo.risk.scoring import BidFacts, RiskSignal, score_bid

CALM = BidFacts(
    seconds_remaining=600,
    extends_close=False,
    previous_bids_in_auction=2,
    previous_extensions_in_auction=0,
    historical_bids=10,
    historical_last_minute_bids=1,
    account_age=timedelta(days=90),
    shares_phone_with_producer=False,
)


def test_an_ordinary_bid_has_no_risk() -> None:
    result = score_bid(CALM)

    assert result.score == 0
    assert result.signals == ()


def test_an_accomplice_at_the_close_scores_high() -> None:
    facts = replace(
        CALM,
        seconds_remaining=20,
        previous_bids_in_auction=0,
        account_age=timedelta(hours=2),
        shares_phone_with_producer=True,
    )

    result = score_bid(facts)

    # 15 (last 30 s) + 10 (first bid at close) + 15 (new account) + 40 (shared phone)
    assert result.score == 80
    assert RiskSignal.SHARES_PHONE_WITH_PRODUCER in result.signals


def test_the_last_10_seconds_replace_the_last_30_seconds_signal() -> None:
    signals = score_bid(replace(CALM, seconds_remaining=5)).signals

    assert RiskSignal.LAST_10_SECONDS in signals
    assert RiskSignal.LAST_30_SECONDS not in signals


def test_a_habit_needs_enough_history() -> None:
    few_bids = replace(CALM, historical_bids=3, historical_last_minute_bids=3)
    habit = replace(CALM, historical_bids=10, historical_last_minute_bids=8)

    assert RiskSignal.LAST_MINUTE_HABIT not in score_bid(few_bids).signals
    assert RiskSignal.LAST_MINUTE_HABIT in score_bid(habit).signals


def test_the_score_never_exceeds_100() -> None:
    everything = BidFacts(
        seconds_remaining=1,
        extends_close=True,
        previous_bids_in_auction=0,
        previous_extensions_in_auction=5,
        historical_bids=10,
        historical_last_minute_bids=10,
        account_age=timedelta(minutes=1),
        shares_phone_with_producer=True,
    )

    assert score_bid(everything).score == 100
