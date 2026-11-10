from datetime import timedelta
from typing import Any

import httpx
import pytest

from subastacampo.v1 import common_pb2
from tests.conftest import TODAY
from tests.rpc import call, error_detail, sign_up

pytestmark = pytest.mark.anyio

PRODUCER = "USER_ROLE_PRODUCER"
BUYER = "USER_ROLE_BUYER"


def product_body(**overrides: Any) -> dict[str, Any]:
    body = {
        "name": "Papa pastusa",
        "description": "Primera calidad",
        "category": "PRODUCT_CATEGORY_TUBERS",
        "quantity": 20,
        "unit": "QUANTITY_UNIT_SACK",
        "harvestDate": (TODAY - timedelta(days=2)).isoformat(),
        "shelfLifeDays": 30,
    }
    return {**body, **overrides}


async def create_product(api: httpx.AsyncClient, token: str, **overrides: Any) -> httpx.Response:
    return await call(
        api, "CatalogService", "CreateProduct", product_body(**overrides), token=token
    )


async def test_producer_publishes_a_product(api: httpx.AsyncClient) -> None:
    token = await sign_up(api, role=PRODUCER, email="productor@test.com")

    response = await create_product(api, token)

    assert response.status_code == 200
    product = response.json()["product"]
    assert product["name"] == "Papa pastusa"
    assert product["category"] == "PRODUCT_CATEGORY_TUBERS"
    # Harvested 2 days ago with 30 days of shelf life.
    assert product["remainingShelfLifeDays"] == 28


async def test_buyer_cannot_publish(api: httpx.AsyncClient) -> None:
    token = await sign_up(api, role=BUYER, email="comprador@test.com")

    response = await create_product(api, token)

    assert response.status_code == 403
    assert error_detail(response).reason == common_pb2.ERROR_REASON_PRODUCER_ONLY


async def test_anonymous_user_cannot_publish(api: httpx.AsyncClient) -> None:
    response = await call(api, "CatalogService", "CreateProduct", product_body())

    assert response.status_code == 401
    assert error_detail(response).reason == common_pb2.ERROR_REASON_AUTHENTICATION_REQUIRED


async def test_future_harvest_date_is_rejected(api: httpx.AsyncClient) -> None:
    token = await sign_up(api, role=PRODUCER, email="productor@test.com")

    response = await create_product(api, token, harvestDate=(TODAY + timedelta(days=1)).isoformat())

    assert response.status_code == 400
    detail = error_detail(response)
    assert detail.reason == common_pb2.ERROR_REASON_HARVEST_DATE_IN_FUTURE
    assert detail.field == "harvest_date"


async def test_quantity_must_be_positive(api: httpx.AsyncClient) -> None:
    token = await sign_up(api, role=PRODUCER, email="productor@test.com")

    response = await create_product(api, token, quantity=0)

    assert response.status_code == 400
    assert error_detail(response).field == "quantity"


async def test_unknown_product_is_not_found(api: httpx.AsyncClient) -> None:
    response = await call(api, "CatalogService", "GetProduct", {"productId": "not-a-uuid"})

    assert response.status_code == 404
    assert error_detail(response).reason == common_pb2.ERROR_REASON_PRODUCT_NOT_FOUND


async def test_listing_is_paginated_newest_first(api: httpx.AsyncClient) -> None:
    token = await sign_up(api, role=PRODUCER, email="productor@test.com")
    for name in ("Producto A", "Producto B", "Producto C"):
        await create_product(api, token, name=name)

    first = await call(api, "CatalogService", "ListProducts", {"pageSize": 2})
    first_page = first.json()
    assert [p["name"] for p in first_page["products"]] == ["Producto C", "Producto B"]
    assert first_page["nextPageToken"]

    second = await call(
        api,
        "CatalogService",
        "ListProducts",
        {"pageSize": 2, "pageToken": first_page["nextPageToken"]},
    )
    second_page = second.json()
    assert [p["name"] for p in second_page["products"]] == ["Producto A"]
    # Proto3 JSON omits empty strings: no token means no more pages.
    assert not second_page.get("nextPageToken")


async def test_only_mine_returns_the_callers_products(api: httpx.AsyncClient) -> None:
    mine = await sign_up(api, role=PRODUCER, email="yo@test.com")
    other = await sign_up(api, role=PRODUCER, email="otro@test.com")
    await create_product(api, mine, name="Mi papa")
    await create_product(api, other, name="Papa ajena")

    response = await call(api, "CatalogService", "ListProducts", {"onlyMine": True}, token=mine)

    assert [p["name"] for p in response.json()["products"]] == ["Mi papa"]


async def test_listing_filters_by_category(api: httpx.AsyncClient) -> None:
    token = await sign_up(api, role=PRODUCER, email="productor@test.com")
    await create_product(api, token, name="Papa", category="PRODUCT_CATEGORY_TUBERS")
    await create_product(api, token, name="Mora", category="PRODUCT_CATEGORY_FRUITS")

    response = await call(
        api, "CatalogService", "ListProducts", {"category": "PRODUCT_CATEGORY_FRUITS"}
    )

    assert [p["name"] for p in response.json()["products"]] == ["Mora"]


async def test_owner_gets_an_explained_price_suggestion(api: httpx.AsyncClient) -> None:
    token = await sign_up(api, role=PRODUCER, email="productor@test.com")
    product_id = (await create_product(api, token)).json()["product"]["id"]

    response = await call(
        api, "CatalogService", "SuggestPrice", {"productId": product_id}, token=token
    )

    assert response.status_code == 200
    suggestion = response.json()["suggestion"]
    # Proto3 JSON encodes int64 as a string to avoid precision loss in JavaScript.
    low = int(suggestion["rangeMin"]["units"])
    suggested = int(suggestion["suggestedPrice"]["units"])
    high = int(suggestion["rangeMax"]["units"])
    assert 0 < low <= suggested <= high
    assert suggestion["quantityKg"] == 1000
    assert suggestion["modelVersion"].startswith("price-v2-")
    codes = {factor["code"] for factor in suggestion["factors"]}
    assert "PRICE_FACTOR_CODE_LARGE_VOLUME" in codes


async def test_only_the_owner_gets_a_price_suggestion(api: httpx.AsyncClient) -> None:
    owner = await sign_up(api, role=PRODUCER, email="duena@test.com")
    other = await sign_up(api, role=PRODUCER, email="otro@test.com")
    product_id = (await create_product(api, owner)).json()["product"]["id"]

    response = await call(
        api, "CatalogService", "SuggestPrice", {"productId": product_id}, token=other
    )

    assert response.status_code == 403
    assert error_detail(response).reason == common_pb2.ERROR_REASON_NOT_PRODUCT_OWNER
