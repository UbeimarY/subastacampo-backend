"""Harvest publication and product listing."""

import math
import uuid
from datetime import date
from decimal import Decimal
from typing import Any

from connectrpc.code import Code
from connectrpc.errors import ConnectError
from connectrpc.request import RequestContext
from sqlalchemy import select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from subastacampo.auth.principal import require_principal
from subastacampo.calendar import BusinessCalendar
from subastacampo.db.models import Product, UserRole
from subastacampo.errors import rpc_error, validation_error
from subastacampo.mappers import CATEGORY_FROM_PROTO, UNIT_FROM_PROTO, to_product_message
from subastacampo.pagination import Cursor, decode_cursor, encode_cursor, resolve_page_size
from subastacampo.v1 import common_pb2
from subastacampo.v1.catalog_connect import CatalogService
from subastacampo.v1.catalog_pb2 import (
    CreateProductRequest,
    CreateProductResponse,
    GetProductRequest,
    GetProductResponse,
    ListProductsRequest,
    ListProductsResponse,
    SuggestPriceRequest,
    SuggestPriceResponse,
)

MAX_QUANTITY = Decimal("1000000")
_CENT = Decimal("0.01")


class CatalogServiceImpl(CatalogService):
    def __init__(
        self, session_factory: async_sessionmaker[AsyncSession], calendar: BusinessCalendar
    ) -> None:
        self._session_factory = session_factory
        self._calendar = calendar

    async def create_product(
        self,
        request: CreateProductRequest,
        ctx: RequestContext[CreateProductRequest, CreateProductResponse],
    ) -> CreateProductResponse:
        principal = require_principal(role=UserRole.PRODUCER)
        today = self._calendar.today()
        product = Product(producer_id=principal.user_id, **_validated_fields(request, today))

        async with self._session_factory() as session:
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
        # Implemented in the next step, together with the pricing model.
        raise ConnectError(Code.UNIMPLEMENTED, "Price suggestion is not available yet")


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
