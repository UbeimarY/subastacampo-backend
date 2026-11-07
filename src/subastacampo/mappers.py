"""Conversions between database models and contract messages."""

from datetime import date, datetime, timedelta

from google.protobuf.timestamp_pb2 import Timestamp

from subastacampo.auctions.rules import minimum_next_bid
from subastacampo.db.models import (
    Auction,
    AuctionStatus,
    Bid,
    Product,
    ProductCategory,
    QuantityUnit,
    User,
    UserRole,
)
from subastacampo.errors import validation_error
from subastacampo.pricing.explanation import Factor, FactorCode
from subastacampo.pricing.features import PriceFeatures
from subastacampo.pricing.model import PricePerKg
from subastacampo.v1 import auction_pb2, catalog_pb2, common_pb2
from subastacampo.v1.auction_pb2 import Auction as AuctionMessage
from subastacampo.v1.auction_pb2 import Bid as BidMessage
from subastacampo.v1.auction_pb2 import RiskAssessment
from subastacampo.v1.auth_pb2 import User as UserMessage
from subastacampo.v1.catalog_pb2 import PriceFactor, PriceSuggestion
from subastacampo.v1.catalog_pb2 import Product as ProductMessage
from subastacampo.v1.common_pb2 import Money

_ROLE_TO_PROTO = {
    UserRole.PRODUCER: common_pb2.USER_ROLE_PRODUCER,
    UserRole.BUYER: common_pb2.USER_ROLE_BUYER,
}
ROLE_FROM_PROTO = {proto: role for role, proto in _ROLE_TO_PROTO.items()}


def to_timestamp(value: datetime) -> Timestamp:
    timestamp = Timestamp()
    timestamp.FromDatetime(value)
    return timestamp


def to_user_message(user: User) -> UserMessage:
    message = UserMessage(
        id=str(user.id),
        full_name=user.full_name,
        email=user.email,
        role=_ROLE_TO_PROTO[user.role],
        created_at=to_timestamp(user.created_at),
    )
    if user.phone is not None:
        message.phone = user.phone
    if user.municipality is not None:
        message.municipality = user.municipality
    return message


_CATEGORY_TO_PROTO = {
    ProductCategory.FRUITS: common_pb2.PRODUCT_CATEGORY_FRUITS,
    ProductCategory.VEGETABLES: common_pb2.PRODUCT_CATEGORY_VEGETABLES,
    ProductCategory.TUBERS: common_pb2.PRODUCT_CATEGORY_TUBERS,
    ProductCategory.GRAINS: common_pb2.PRODUCT_CATEGORY_GRAINS,
    ProductCategory.DAIRY: common_pb2.PRODUCT_CATEGORY_DAIRY,
    ProductCategory.OTHER: common_pb2.PRODUCT_CATEGORY_OTHER,
}
CATEGORY_FROM_PROTO = {proto: category for category, proto in _CATEGORY_TO_PROTO.items()}

_UNIT_TO_PROTO = {
    QuantityUnit.KILOGRAM: common_pb2.QUANTITY_UNIT_KILOGRAM,
    QuantityUnit.ARROBA: common_pb2.QUANTITY_UNIT_ARROBA,
    QuantityUnit.SACK: common_pb2.QUANTITY_UNIT_SACK,
    QuantityUnit.CRATE: common_pb2.QUANTITY_UNIT_CRATE,
}
UNIT_FROM_PROTO = {proto: unit for unit, proto in _UNIT_TO_PROTO.items()}


def remaining_shelf_life_days(product: Product, *, today: date) -> int:
    expires_on = product.harvest_date + timedelta(days=product.shelf_life_days)
    return max(0, (expires_on - today).days)


def to_product_message(product: Product, *, today: date) -> ProductMessage:
    return ProductMessage(
        id=str(product.id),
        producer_id=str(product.producer_id),
        name=product.name,
        description=product.description,
        category=_CATEGORY_TO_PROTO[product.category],
        quantity=float(product.quantity),
        unit=_UNIT_TO_PROTO[product.unit],
        harvest_date=product.harvest_date.isoformat(),
        shelf_life_days=product.shelf_life_days,
        remaining_shelf_life_days=remaining_shelf_life_days(product, today=today),
        created_at=to_timestamp(product.created_at),
    )


_FACTOR_TO_PROTO = {
    FactorCode.FRESH_PRODUCT: catalog_pb2.PRICE_FACTOR_CODE_FRESH_PRODUCT,
    FactorCode.SHORT_SHELF_LIFE: catalog_pb2.PRICE_FACTOR_CODE_SHORT_SHELF_LIFE,
    FactorCode.HIGH_DEMAND: catalog_pb2.PRICE_FACTOR_CODE_HIGH_DEMAND,
    FactorCode.LOW_DEMAND: catalog_pb2.PRICE_FACTOR_CODE_LOW_DEMAND,
    FactorCode.LARGE_VOLUME: catalog_pb2.PRICE_FACTOR_CODE_LARGE_VOLUME,
}


def money(units: int) -> Money:
    return Money(currency_code="COP", units=units)


def round_to(value: float, step: int) -> int:
    return int(round(value / step)) * step


def to_price_suggestion(
    features: PriceFeatures,
    prediction: PricePerKg,
    factors: list[Factor],
    *,
    model_version: str,
) -> PriceSuggestion:
    kg = features.quantity_kg
    return PriceSuggestion(
        suggested_price=money(round_to(prediction.median * kg, 100)),
        range_min=money(round_to(prediction.low * kg, 100)),
        range_max=money(round_to(prediction.high * kg, 100)),
        price_per_kg=money(round_to(prediction.median, 10)),
        quantity_kg=kg,
        category_demand=features.category_demand,
        factors=[PriceFactor(code=_FACTOR_TO_PROTO[f.code], value=f.value) for f in factors],
        model_version=model_version,
    )


CURRENCY = "COP"

_STATUS_TO_PROTO = {
    AuctionStatus.SCHEDULED: auction_pb2.AUCTION_STATUS_SCHEDULED,
    AuctionStatus.ACTIVE: auction_pb2.AUCTION_STATUS_ACTIVE,
    AuctionStatus.CLOSED: auction_pb2.AUCTION_STATUS_CLOSED,
    AuctionStatus.CANCELLED: auction_pb2.AUCTION_STATUS_CANCELLED,
}
STATUS_FROM_PROTO = {proto: status for status, proto in _STATUS_TO_PROTO.items()}

MEDIUM_RISK_SCORE = 30
HIGH_RISK_SCORE = 60


def money_units(value: Money, field: str) -> int:
    """Reads an incoming amount. Only Colombian pesos are supported for now."""
    if value.currency_code not in ("", CURRENCY):
        raise validation_error(field, "Only COP amounts are supported")
    return value.units


def risk_level(score: int) -> int:
    if score >= HIGH_RISK_SCORE:
        return auction_pb2.RISK_LEVEL_HIGH
    if score >= MEDIUM_RISK_SCORE:
        return auction_pb2.RISK_LEVEL_MEDIUM
    return auction_pb2.RISK_LEVEL_LOW


def to_auction_message(auction: Auction, *, today: date) -> AuctionMessage:
    """The auction's product must already be loaded."""
    message = AuctionMessage(
        id=str(auction.id),
        product=to_product_message(auction.product, today=today),
        starting_price=money(auction.starting_price),
        min_increment=money(auction.min_increment),
        current_price=money(auction.current_price),
        next_min_bid=money(
            minimum_next_bid(
                starting_price=auction.starting_price,
                current_price=auction.current_price,
                min_increment=auction.min_increment,
                bid_count=auction.bid_count,
            )
        ),
        starts_at=to_timestamp(auction.starts_at),
        ends_at=to_timestamp(auction.ends_at),
        status=_STATUS_TO_PROTO[auction.status],
        extension_count=auction.extension_count,
        bid_count=auction.bid_count,
    )
    if auction.winner_id is not None:
        message.winner_id = str(auction.winner_id)
    return message


def to_bid_message(bid: Bid, *, include_risk: bool) -> BidMessage:
    message = BidMessage(
        id=str(bid.id),
        auction_id=str(bid.auction_id),
        bidder_id=str(bid.bidder_id),
        bidder_number=bid.bidder_number,
        amount=money(bid.amount),
        placed_at=to_timestamp(bid.placed_at),
        extended_close=bid.extended_close,
    )
    if include_risk:
        message.risk.CopyFrom(
            RiskAssessment(
                score=bid.risk_score,
                level=risk_level(bid.risk_score),
                signals=[
                    auction_pb2.RiskSignal.Value(f"RISK_SIGNAL_{signal}")
                    for signal in bid.risk_signals
                ],
            )
        )
    return message
