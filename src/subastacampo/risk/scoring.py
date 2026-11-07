"""Risk score of a bid: explainable rules over observable facts.

Deliberately not machine learning yet: there are no labeled fraud cases to learn
from. Every bid stores its signals, building the dataset a future model needs.
"""

import enum
from dataclasses import dataclass
from datetime import timedelta

NEW_ACCOUNT_AGE = timedelta(hours=24)
HABIT_MIN_BIDS = 5
HABIT_RATIO = 0.7


class RiskSignal(enum.StrEnum):
    """Names match the contract's RiskSignal values without their prefix."""

    LAST_10_SECONDS = "LAST_10_SECONDS"
    LAST_30_SECONDS = "LAST_30_SECONDS"
    FIRST_BID_AT_CLOSE = "FIRST_BID_AT_CLOSE"
    REPEATED_EXTENSIONS = "REPEATED_EXTENSIONS"
    NEW_ACCOUNT = "NEW_ACCOUNT"
    LAST_MINUTE_HABIT = "LAST_MINUTE_HABIT"
    SHARES_PHONE_WITH_PRODUCER = "SHARES_PHONE_WITH_PRODUCER"


WEIGHTS = {
    RiskSignal.LAST_10_SECONDS: 30,
    RiskSignal.LAST_30_SECONDS: 15,
    RiskSignal.FIRST_BID_AT_CLOSE: 10,
    RiskSignal.REPEATED_EXTENSIONS: 25,
    RiskSignal.NEW_ACCOUNT: 15,
    RiskSignal.LAST_MINUTE_HABIT: 20,
    RiskSignal.SHARES_PHONE_WITH_PRODUCER: 40,
}


@dataclass(frozen=True, slots=True)
class BidFacts:
    seconds_remaining: float
    extends_close: bool
    previous_bids_in_auction: int
    previous_extensions_in_auction: int
    historical_bids: int
    historical_last_minute_bids: int
    account_age: timedelta
    shares_phone_with_producer: bool


@dataclass(frozen=True, slots=True)
class RiskResult:
    score: int
    signals: tuple[RiskSignal, ...]


def score_bid(facts: BidFacts) -> RiskResult:
    signals: list[RiskSignal] = []

    if facts.seconds_remaining <= 10:
        signals.append(RiskSignal.LAST_10_SECONDS)
    elif facts.seconds_remaining <= 30:
        signals.append(RiskSignal.LAST_30_SECONDS)

    if facts.previous_bids_in_auction == 0 and facts.seconds_remaining <= 30:
        signals.append(RiskSignal.FIRST_BID_AT_CLOSE)

    if facts.extends_close and facts.previous_extensions_in_auction >= 2:
        signals.append(RiskSignal.REPEATED_EXTENSIONS)

    if facts.account_age < NEW_ACCOUNT_AGE:
        signals.append(RiskSignal.NEW_ACCOUNT)

    if (
        facts.historical_bids >= HABIT_MIN_BIDS
        and facts.historical_last_minute_bids / facts.historical_bids >= HABIT_RATIO
    ):
        signals.append(RiskSignal.LAST_MINUTE_HABIT)

    if facts.shares_phone_with_producer:
        signals.append(RiskSignal.SHARES_PHONE_WITH_PRODUCER)

    score = min(100, sum(WEIGHTS[signal] for signal in signals))
    return RiskResult(score=score, signals=tuple(signals))
