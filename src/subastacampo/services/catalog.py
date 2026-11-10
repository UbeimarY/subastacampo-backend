"""Harvest publication, product listing and AI-assisted pricing."""

import asyncio
import math
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import Any

from connectrpc.code import Code
from connectrpc.errors import ConnectError
from connectrpc.request import RequestContext
from sqlalchemy import func, select, true, tuple_
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from subastacampo.auctions.broker import AuctionEventBroker, SubscriptionOverflowError
from subastacampo.auth.principal import require_principal
from subastacampo.calendar import BusinessCalendar
from subastacampo.clock import Clock
from subastacampo.db.models import (
    Auction,
    AuctionStatus,
    Product,
    ProductCategory,
    User,
    UserRole,
)
from subastacampo.errors import rpc_error, validation_error
from subastacampo.mappers import (
    CATEGORY_FROM_PROTO,
    UNIT_FROM_PROTO,
    to_category_market,
    to_price_suggestion,
    to_product_message,
    to_timestamp,
)
from subastacampo.pagination import Cursor, decode_cursor, encode_cursor, resolve_page_size
from subastacampo.pricing.comparables import Sale, closest_sales
from subastacampo.pricing.explanation import explain
from subastacampo.pricing.features import DEFAULT_CATEGORY_DEMAND, KG_PER_UNIT, build_features
from subastacampo.pricing.market import period_days, summarize
from subastacampo.pricing.model import PricingModel
from subastacampo.v1 import common_pb2
from subastacampo.v1.catalog_connect import CatalogService
from subastacampo.v1.catalog_pb2 import (
    CategoryMarket,
    CreateProductRequest,
    CreateProductResponse,
    GetMarketPricesRequest,
    GetMarketPricesResponse,
    GetProductRequest,
    GetProductResponse,
    ListProductsRequest,
    ListProductsResponse,
    SuggestPriceRequest,
    SuggestPriceResponse,
    WatchMarketPricesRequest,
    WatchMarketPricesResponse,
)

MAX_QUANTITY = Decimal("1000000")
_CENT = Decimal("0.01")
DEMAND_WINDOW = timedelta(days=30)
# The market stream sends fresh prices at least this often, even without closes.
MARKET_REFRESH_SECONDS = 300.0
# How far back closed auctions count as comparable sales.
COMPARABLES_WINDOW = timedelta(days=90)


class CatalogServiceImpl(CatalogService):
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        calendar: BusinessCalendar,
        pricing: PricingModel,
        broker: AuctionEventBroker | None = None,
        clock: Clock | None = None,
        market_refresh_seconds: float = MARKET_REFRESH_SECONDS,
    ) -> None:
        self._session_factory = session_factory
        self._calendar = calendar
        self._pricing = pricing
        self._broker = broker or AuctionEventBroker()
        self._clock = clock
        self._market_refresh_seconds = market_refresh_seconds

    async def create_product(
        self,
        request: CreateProductRequest,
        ctx: RequestContext[CreateProductRequest, CreateProductResponse],
    ) -> CreateProductResponse:
        principal = require_principal(role=UserRole.PRODUCER)
        today = self._calendar.today()
        product = Product(producer_id=principal.user_id, **_validated_fields(request, today))

        async with self._session_factory() as session:
            producer = await session.get(User, principal.user_id)
            product.origin = producer.municipality if producer else None
            session.add(product)
            await session.commit()
            await session.refresh(product)

        return CreateProductResponse(product=to_product_message(product, today=today))

    async def get_product(
        self,
        request: GetProductRequest,
        ctx: RequestContext[GetProductRequest, GetProductResponse],
    ) -> GetProductResponse:
        product_id = _parse_product_id(request.product_id)
        async with self._session_factory() as session:
            product = await session.get(Product, product_id)
        if product is None:
            raise _product_not_found()
        return GetProductResponse(product=to_product_message(product, today=self._calendar.today()))

    async def list_products(
        self,
        request: ListProductsRequest,
        ctx: RequestContext[ListProductsRequest, ListProductsResponse],
    ) -> ListProductsResponse:
        size = resolve_page_size(request.page_size)
        cursor = decode_cursor(request.page_token)

        # One extra row tells us whether another page exists, without a COUNT query.
        query = (
            select(Product).order_by(Product.created_at.desc(), Product.id.desc()).limit(size + 1)
        )
        if request.category != common_pb2.PRODUCT_CATEGORY_UNSPECIFIED:
            category = CATEGORY_FROM_PROTO.get(request.category)
            if category is None:
                raise validation_error("category", "Unknown product category")
            query = query.where(Product.category == category)
        if request.only_mine:
            principal = require_principal(role=UserRole.PRODUCER)
            query = query.where(Product.producer_id == principal.user_id)
        if cursor is not None:
            query = query.where(
                tuple_(Product.created_at, Product.id) < (cursor.created_at, cursor.id)
            )

        async with self._session_factory() as session:
            products = list((await session.scalars(query)).all())

        next_page_token = ""
        if len(products) > size:
            products = products[:size]
            last = products[-1]
            next_page_token = encode_cursor(Cursor(created_at=last.created_at, id=last.id))

        today = self._calendar.today()
        return ListProductsResponse(
            products=[to_product_message(product, today=today) for product in products],
            next_page_token=next_page_token,
        )

    async def suggest_price(
        self,
        request: SuggestPriceRequest,
        ctx: RequestContext[SuggestPriceRequest, SuggestPriceResponse],
    ) -> SuggestPriceResponse:
        principal = require_principal(role=UserRole.PRODUCER)
        product_id = _parse_product_id(request.product_id)

        async with self._session_factory() as session:
            product = await session.get(Product, product_id)
            if product is None:
                raise _product_not_found()
            if product.producer_id != principal.user_id:
                raise rpc_error(
                    Code.PERMISSION_DENIED,
                    common_pb2.ERROR_REASON_NOT_PRODUCT_OWNER,
                    "Only the product's producer can request a price suggestion",
                )
            demand = await _category_demand(session, product.category)
            sales = await _recent_sales(
                session,
                product.category,
                exclude=product.id,
                since=datetime.combine(self._calendar.today(), time.min, UTC) - COMPARABLES_WINDOW,
            )

        features = build_features(
            category=product.category,
            quantity=float(product.quantity),
            unit=product.unit,
            harvest_date=product.harvest_date,
            shelf_life_days=product.shelf_life_days,
            today=self._calendar.today(),
            category_demand=demand if demand is not None else DEFAULT_CATEGORY_DEMAND,
        )

        # CPU-bound inference runs in a worker thread: the event loop stays free
        # to serve live auction streams meanwhile.
        prediction = await asyncio.to_thread(self._pricing.predict, features)

        suggestion = to_price_suggestion(
            features,
            prediction,
            explain(features, has_demand_data=demand is not None),
            model_version=self._pricing.version,
            coverage=self._pricing.coverage,
            comparables=closest_sales(sales, features.quantity_kg),
        )
        return SuggestPriceResponse(suggestion=suggestion)

    async def get_market_prices(
        self,
        request: GetMarketPricesRequest,
        ctx: RequestContext[GetMarketPricesRequest, GetMarketPricesResponse],
    ) -> GetMarketPricesResponse:
        days = period_days(request.days)
        categories = await self._market(days)
        return GetMarketPricesResponse(categories=categories, days=days)

    async def watch_market_prices(
        self,
        request: WatchMarketPricesRequest,
        ctx: RequestContext[WatchMarketPricesRequest, WatchMarketPricesResponse],
    ) -> AsyncIterator[WatchMarketPricesResponse]:
        days = period_days(request.days)
        # Subscribe first: a close that happens while the first answer is computed
        # still triggers the next one.
        with self._broker.subscribe_market() as subscription:
            while True:
                yield WatchMarketPricesResponse(
                    categories=await self._market(days),
                    days=days,
                    updated_at=to_timestamp(self._now()),
                )
                try:
                    async with asyncio.timeout(self._market_refresh_seconds):
                        await subscription.next()
                    # Several closes at once (the closer runs in batches): one answer.
                    subscription.drain()
                except TimeoutError:
                    pass  # periodic refresh, which also keeps proxies from closing the stream
                except SubscriptionOverflowError as error:
                    raise ConnectError(
                        Code.ABORTED, "Too many pending events: reconnect to resume"
                    ) from error

    def _now(self) -> datetime:
        return self._clock.now() if self._clock else datetime.now(UTC)

    async def _market(self, days: int) -> list[CategoryMarket]:
        since = datetime.combine(self._calendar.today(), time.min, UTC) - timedelta(days=days)
        sales: dict[ProductCategory, list[Sale]] = {}
        async with self._session_factory() as session:
            for category in ProductCategory:
                sales[category] = await _recent_sales(
                    session, category, exclude=None, since=since, limit=1000
                )
        return [to_category_market(summary) for summary in summarize(sales)]


async def _category_demand(session: AsyncSession, category: ProductCategory) -> float | None:
    """Average bids per auction in the category during the demand window."""
    since = datetime.now(UTC) - DEMAND_WINDOW
    average = await session.scalar(
        select(func.avg(Auction.bid_count))
        .join(Product, Auction.product_id == Product.id)
        .where(Product.category == category, Auction.starts_at >= since)
    )
    return float(average) if average is not None else None


async def _recent_sales(
    session: AsyncSession,
    category: ProductCategory,
    *,
    exclude: uuid.UUID | None,
    since: datetime,
    limit: int = 200,
) -> list[Sale]:
    """Closed auctions of the category that sold, since the given moment."""
    rows = await session.execute(
        select(Product.name, Product.quantity, Product.unit, Auction.current_price, Auction.ends_at)
        .join(Product, Auction.product_id == Product.id)
        .where(
            Product.category == category,
            Product.id != exclude if exclude is not None else true(),
            Auction.status == AuctionStatus.CLOSED,
            Auction.winner_id.is_not(None),
            Auction.ends_at >= since,
        )
        .order_by(Auction.ends_at.desc())
        .limit(limit)
    )
    return [
        Sale(
            product_name=name,
            quantity_kg=float(quantity) * KG_PER_UNIT[unit],
            final_price=price,
            closed_at=ends_at,
        )
        for name, quantity, unit, price, ends_at in rows
    ]


def _validated_fields(request: CreateProductRequest, today: date) -> dict[str, Any]:
    name = request.name.strip()
    if not 2 <= len(name) <= 150:
        raise validation_error("name", "Name must have 2 to 150 characters")

    description = request.description.strip()
    if len(description) > 2000:
        raise validation_error("description", "Description must have at most 2000 characters")

    category = CATEGORY_FROM_PROTO.get(request.category)
    if category is None:
        raise validation_error("category", "Category is required")

    unit = UNIT_FROM_PROTO.get(request.unit)
    if unit is None:
        raise validation_error("unit", "Unit is required")

    if not math.isfinite(request.quantity):
        raise validation_error("quantity", "Quantity must be a number")
    quantity = Decimal(str(request.quantity))
    if not (0 < quantity <= MAX_QUANTITY) or quantity != quantity.quantize(_CENT):
        raise validation_error("quantity", "Quantity must be positive, with at most 2 decimals")

    try:
        harvest_date = date.fromisoformat(request.harvest_date)
    except ValueError as error:
        raise validation_error("harvest_date", "Harvest date must be YYYY-MM-DD") from error
    if harvest_date > today:
        raise rpc_error(
            Code.INVALID_ARGUMENT,
            common_pb2.ERROR_REASON_HARVEST_DATE_IN_FUTURE,
            "Harvest date cannot be in the future",
            field="harvest_date",
        )

    if not 1 <= request.shelf_life_days <= 365:
        raise validation_error("shelf_life_days", "Shelf life must be between 1 and 365 days")

    return {
        "name": name,
        "description": description,
        "category": category,
        "quantity": quantity,
        "unit": unit,
        "harvest_date": harvest_date,
        "shelf_life_days": request.shelf_life_days,
    }


def _parse_product_id(raw: str) -> uuid.UUID:
    """A malformed id is reported as not found: it does not reveal anything."""
    try:
        return uuid.UUID(raw)
    except ValueError as error:
        raise _product_not_found() from error


def _product_not_found() -> ConnectError:
    return rpc_error(Code.NOT_FOUND, common_pb2.ERROR_REASON_PRODUCT_NOT_FOUND, "Product not found")
