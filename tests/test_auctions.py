import asyncio
import uuid
from datetime import timedelta
from typing import Any

import httpx
import pytest

from subastacampo.v1 import common_pb2
from tests.conftest import NOW, TODAY, FakeClock
from tests.rpc import call, current_user_id, error_detail, sign_up

pytestmark = pytest.mark.anyio

STARTING_PRICE = 500_000
INCREMENT = 10_000


def cop(units: int) -> dict[str, Any]:
    return {"currencyCode": "COP", "units": str(units)}


async def start_auction(
    api: httpx.AsyncClient, producer: str, *, minutes: int = 10
) -> dict[str, Any]:
    product = await call(
        api,
        "CatalogService",
        "CreateProduct",
        {
            "name": "Papa pastusa",
            "category": "PRODUCT_CATEGORY_TUBERS",
            "quantity": 20,
            "unit": "QUANTITY_UNIT_SACK",
            "harvestDate": (TODAY - timedelta(days=2)).isoformat(),
            "shelfLifeDays": 30,
        },
        token=producer,
    )
    response = await call(
        api,
        "AuctionService",
        "CreateAuction",
        {
            "productId": product.json()["product"]["id"],
            "startingPrice": cop(STARTING_PRICE),
            "minIncrement": cop(INCREMENT),
            "duration": f"{minutes * 60}s",
        },
        token=producer,
    )
    assert response.status_code == 200, response.text
    return response.json()["auction"]


async def bid(
    api: httpx.AsyncClient, buyer: str, auction_id: str, units: int, request_id: str | None = None
) -> httpx.Response:
    return await call(
        api,
        "AuctionService",
        "PlaceBid",
        {
            "auctionId": auction_id,
            "amount": cop(units),
            "requestId": request_id or str(uuid.uuid4()),
        },
        token=buyer,
    )


async def test_producer_starts_an_auction(api: httpx.AsyncClient, producer: str) -> None:
    auction = await start_auction(api, producer)

    assert auction["status"] == "AUCTION_STATUS_ACTIVE"
    assert auction["nextMinBid"]["units"] == str(STARTING_PRICE)


async def test_a_product_cannot_have_two_active_auctions(
    api: httpx.AsyncClient, producer: str
) -> None:
    auction = await start_auction(api, producer)

    response = await call(
        api,
        "AuctionService",
        "CreateAuction",
        {
            "productId": auction["product"]["id"],
            "startingPrice": cop(STARTING_PRICE),
            "minIncrement": cop(INCREMENT),
            "duration": "600s",
        },
        token=producer,
    )

    assert error_detail(response).reason == common_pb2.ERROR_REASON_AUCTION_ALREADY_ACTIVE


async def test_low_bid_is_rejected_with_the_current_minimum(
    api: httpx.AsyncClient, producer: str, buyer: str
) -> None:
    auction = await start_auction(api, producer)

    response = await bid(api, buyer, auction["id"], STARTING_PRICE - 1)

    detail = error_detail(response)
    assert detail.reason == common_pb2.ERROR_REASON_BID_TOO_LOW
    assert detail.minimum_bid.units == STARTING_PRICE


async def test_valid_bids_raise_the_price(
    api: httpx.AsyncClient, producer: str, buyer: str
) -> None:
    auction = await start_auction(api, producer)

    response = await bid(api, buyer, auction["id"], STARTING_PRICE)

    assert response.status_code == 200
    updated = response.json()["auction"]
    assert updated["currentPrice"]["units"] == str(STARTING_PRICE)
    assert updated["nextMinBid"]["units"] == str(STARTING_PRICE + INCREMENT)
    assert response.json()["bid"]["bidderNumber"] == 1


async def test_retrying_a_bid_never_duplicates_it(
    api: httpx.AsyncClient, producer: str, buyer: str
) -> None:
    auction = await start_auction(api, producer)
    request_id = str(uuid.uuid4())

    first = await bid(api, buyer, auction["id"], STARTING_PRICE, request_id)
    retry = await bid(api, buyer, auction["id"], STARTING_PRICE, request_id)

    assert retry.status_code == 200
    assert retry.json()["bid"]["id"] == first.json()["bid"]["id"]
    assert retry.json()["auction"]["bidCount"] == 1


async def test_last_minute_bid_extends_the_close(
    api: httpx.AsyncClient, producer: str, buyer: str, clock: FakeClock
) -> None:
    auction = await start_auction(api, producer, minutes=10)
    clock.advance(timedelta(minutes=9))

    response = await bid(api, buyer, auction["id"], STARTING_PRICE)

    assert response.json()["bid"]["extendedClose"] is True
    assert response.json()["auction"]["extensionCount"] == 1


async def test_simultaneous_bids_are_processed_one_at_a_time(
    api: httpx.AsyncClient, producer: str, buyer: str
) -> None:
    other_buyer = await sign_up(api, role="USER_ROLE_BUYER", email="otro@test.com")
    auction = await start_auction(api, producer)

    # Both send the same amount at the same time: only one can win the row lock first.
    responses = await asyncio.gather(
        bid(api, buyer, auction["id"], STARTING_PRICE),
        bid(api, other_buyer, auction["id"], STARTING_PRICE),
    )

    accepted = [r for r in responses if r.status_code == 200]
    rejected = [r for r in responses if r.status_code != 200]
    assert len(accepted) == 1
    assert len(rejected) == 1
    assert error_detail(rejected[0]).reason == common_pb2.ERROR_REASON_BID_TOO_LOW


async def test_expired_auction_closes_with_the_highest_bidder(
    api: httpx.AsyncClient, producer: str, buyer: str, clock: FakeClock
) -> None:
    auction = await start_auction(api, producer, minutes=10)
    await bid(api, buyer, auction["id"], STARTING_PRICE)
    clock.advance(timedelta(minutes=11))

    response = await call(api, "AuctionService", "GetAuction", {"auctionId": auction["id"]})

    closed = response.json()["auction"]
    assert closed["status"] == "AUCTION_STATUS_CLOSED"
    assert closed["winnerId"] == await current_user_id(api, buyer)


async def test_bids_on_a_closed_auction_are_rejected(
    api: httpx.AsyncClient, producer: str, buyer: str, clock: FakeClock
) -> None:
    auction = await start_auction(api, producer, minutes=10)
    clock.advance(timedelta(minutes=11))

    response = await bid(api, buyer, auction["id"], STARTING_PRICE)

    assert error_detail(response).reason == common_pb2.ERROR_REASON_AUCTION_NOT_ACTIVE


async def test_only_the_owner_sees_the_risk_of_each_bid(
    api: httpx.AsyncClient, producer: str, buyer: str
) -> None:
    auction = await start_auction(api, producer)
    await bid(api, buyer, auction["id"], STARTING_PRICE)

    as_owner = await call(
        api, "AuctionService", "GetAuction", {"auctionId": auction["id"]}, token=producer
    )
    as_buyer = await call(
        api, "AuctionService", "GetAuction", {"auctionId": auction["id"]}, token=buyer
    )

    assert "risk" in as_owner.json()["bids"][0]
    assert "risk" not in as_buyer.json()["bids"][0]


def test_the_test_clock_starts_on_the_business_day() -> None:
    assert NOW.date() == TODAY


async def test_an_accomplice_bid_is_flagged_to_the_owner(
    api: httpx.AsyncClient, clock: FakeClock
) -> None:
    owner = await sign_up(
        api, role="USER_ROLE_PRODUCER", email="duena@test.com", phone="+57 313 522 0484"
    )
    accomplice = await sign_up(
        api, role="USER_ROLE_BUYER", email="complice@test.com", phone="3135220484"
    )
    auction = await start_auction(api, owner, minutes=10)
    clock.advance(timedelta(minutes=9, seconds=40))  # 20 seconds left

    await bid(api, accomplice, auction["id"], STARTING_PRICE)

    response = await call(
        api, "AuctionService", "GetAuction", {"auctionId": auction["id"]}, token=owner
    )
    risk = response.json()["bids"][0]["risk"]
    assert risk["level"] == "RISK_LEVEL_HIGH"
    assert risk["score"] == 80
    assert "RISK_SIGNAL_SHARES_PHONE_WITH_PRODUCER" in risk["signals"]
