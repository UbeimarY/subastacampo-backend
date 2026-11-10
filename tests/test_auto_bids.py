"""Automatic bidding end to end: the server bids for a buyer up to their maximum."""

from typing import Any

import httpx
import pytest

from subastacampo.v1 import common_pb2
from tests.rpc import call, error_detail, sign_up
from tests.test_auctions import INCREMENT, STARTING_PRICE, bid, cop, start_auction

pytestmark = pytest.mark.anyio


async def set_auto(
    api: httpx.AsyncClient, token: str, auction_id: str, units: int
) -> httpx.Response:
    return await call(
        api,
        "AuctionService",
        "SetAutoBid",
        {"auctionId": auction_id, "maxAmount": cop(units)},
        token=token,
    )


async def bids_of(api: httpx.AsyncClient, auction_id: str) -> list[dict[str, Any]]:
    response = await call(api, "AuctionService", "GetAuction", {"auctionId": auction_id})
    return response.json().get("bids", [])


async def test_a_maximum_opens_the_auction_and_answers_a_rival(
    api: httpx.AsyncClient, producer: str, buyer: str
) -> None:
    rival = await sign_up(api, role="USER_ROLE_BUYER", email="rival@test.com")
    auction = await start_auction(api, producer)
    maximum = STARTING_PRICE + 5 * INCREMENT

    opened = await set_auto(api, buyer, auction["id"], maximum)
    assert opened.status_code == 200
    assert opened.json()["auction"]["currentPrice"]["units"] == str(STARTING_PRICE)

    answered = await bid(api, rival, auction["id"], STARTING_PRICE + INCREMENT)
    assert answered.status_code == 200
    # The rival's bid stands, but the automatic bid has already beaten it.
    assert answered.json()["auction"]["currentPrice"]["units"] == str(
        STARTING_PRICE + 2 * INCREMENT
    )

    highest = max(await bids_of(api, auction["id"]), key=lambda b: int(b["amount"]["units"]))
    assert highest["automatic"] is True


async def test_the_maximum_stays_private_and_stops_at_its_limit(
    api: httpx.AsyncClient, producer: str, buyer: str
) -> None:
    rival = await sign_up(api, role="USER_ROLE_BUYER", email="rival@test.com")
    auction = await start_auction(api, producer)
    maximum = STARTING_PRICE + 2 * INCREMENT
    await set_auto(api, buyer, auction["id"], maximum)

    await bid(api, rival, auction["id"], STARTING_PRICE + 3 * INCREMENT)

    mine = await call(
        api, "AuctionService", "GetAutoBid", {"auctionId": auction["id"]}, token=buyer
    )
    assert mine.json()["autoBid"]["exhausted"] is True
    assert all(
        int(b["amount"]["units"]) <= STARTING_PRICE + 3 * INCREMENT
        for b in await bids_of(api, auction["id"])
    )
    theirs = await call(
        api, "AuctionService", "GetAutoBid", {"auctionId": auction["id"]}, token=rival
    )
    assert "autoBid" not in theirs.json()


async def test_two_maxima_settle_one_increment_above_the_lower_one(
    api: httpx.AsyncClient, producer: str, buyer: str
) -> None:
    rival = await sign_up(api, role="USER_ROLE_BUYER", email="rival@test.com")
    auction = await start_auction(api, producer)
    await set_auto(api, buyer, auction["id"], STARTING_PRICE + 10 * INCREMENT)

    response = await set_auto(api, rival, auction["id"], STARTING_PRICE + 4 * INCREMENT)

    assert response.json()["auction"]["currentPrice"]["units"] == str(
        STARTING_PRICE + 5 * INCREMENT
    )
    assert response.json()["autoBid"]["exhausted"] is True


async def test_a_maximum_that_cannot_beat_the_price_is_refused(
    api: httpx.AsyncClient, producer: str, buyer: str
) -> None:
    rival = await sign_up(api, role="USER_ROLE_BUYER", email="rival@test.com")
    auction = await start_auction(api, producer)
    await bid(api, rival, auction["id"], STARTING_PRICE + 3 * INCREMENT)

    response = await set_auto(api, buyer, auction["id"], STARTING_PRICE + 3 * INCREMENT)

    assert response.status_code == 400 or response.status_code == 412
    assert error_detail(response).reason == common_pb2.ERROR_REASON_BID_TOO_LOW


async def test_cancelling_stops_further_automatic_bids(
    api: httpx.AsyncClient, producer: str, buyer: str
) -> None:
    rival = await sign_up(api, role="USER_ROLE_BUYER", email="rival@test.com")
    auction = await start_auction(api, producer)
    await set_auto(api, buyer, auction["id"], STARTING_PRICE + 10 * INCREMENT)
    await call(api, "AuctionService", "CancelAutoBid", {"auctionId": auction["id"]}, token=buyer)

    response = await bid(api, rival, auction["id"], STARTING_PRICE + INCREMENT)

    assert response.json()["auction"]["currentPrice"]["units"] == str(STARTING_PRICE + INCREMENT)


async def test_producers_cannot_bid_automatically(api: httpx.AsyncClient, producer: str) -> None:
    auction = await start_auction(api, producer)

    response = await set_auto(api, producer, auction["id"], STARTING_PRICE * 2)

    assert response.status_code == 403
