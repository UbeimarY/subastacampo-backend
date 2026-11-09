from datetime import timedelta

import httpx
import pytest

from subastacampo.v1 import common_pb2
from tests.conftest import FakeClock
from tests.rpc import call, error_detail, sign_up
from tests.test_auctions import bid, start_auction

pytestmark = pytest.mark.anyio


async def contact(api: httpx.AsyncClient, token: str, auction_id: str) -> httpx.Response:
    return await call(
        api, "AuctionService", "GetAuctionContact", {"auctionId": auction_id}, token=token
    )


async def closed_auction(
    api: httpx.AsyncClient, producer: str, buyer: str, clock: FakeClock
) -> str:
    auction = await start_auction(api, producer)
    await bid(api, buyer, auction["id"], 500_000)
    clock.advance(timedelta(minutes=11))
    return str(auction["id"])


async def test_the_producer_gets_the_winners_contact(
    api: httpx.AsyncClient, producer: str, clock: FakeClock
) -> None:
    winner = await sign_up(
        api, role="USER_ROLE_BUYER", email="ganador@test.com", phone="3001234567"
    )
    auction_id = await closed_auction(api, producer, winner, clock)

    response = await contact(api, producer, auction_id)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["contact"]["role"] == "USER_ROLE_BUYER"
    assert body["contact"]["email"] == "ganador@test.com"
    assert body["contact"]["phone"] == "3001234567"
    assert body["finalPrice"]["units"] == "500000"


async def test_the_winner_gets_the_producers_contact(
    api: httpx.AsyncClient, producer: str, buyer: str, clock: FakeClock
) -> None:
    auction_id = await closed_auction(api, producer, buyer, clock)

    response = await contact(api, buyer, auction_id)

    assert response.status_code == 200, response.text
    assert response.json()["contact"]["role"] == "USER_ROLE_PRODUCER"
    assert response.json()["contact"]["email"] == "productor@test.com"


async def test_nothing_is_shared_while_the_auction_is_open(
    api: httpx.AsyncClient, producer: str, buyer: str
) -> None:
    auction = await start_auction(api, producer)
    await bid(api, buyer, auction["id"], 500_000)

    response = await contact(api, buyer, auction["id"])

    assert response.status_code == 400
    assert error_detail(response).reason == common_pb2.ERROR_REASON_AUCTION_HAS_NO_WINNER


async def test_a_losing_bidder_learns_nothing(
    api: httpx.AsyncClient, producer: str, buyer: str, clock: FakeClock
) -> None:
    loser = await sign_up(api, role="USER_ROLE_BUYER", email="perdedor@test.com")
    auction = await start_auction(api, producer)
    await bid(api, loser, auction["id"], 500_000)
    await bid(api, buyer, auction["id"], 510_000)
    clock.advance(timedelta(minutes=11))

    response = await contact(api, loser, auction["id"])

    assert response.status_code == 403
    assert error_detail(response).reason == common_pb2.ERROR_REASON_NOT_AUCTION_PARTY


async def test_an_auction_without_bids_has_no_contact(
    api: httpx.AsyncClient, producer: str, clock: FakeClock
) -> None:
    auction = await start_auction(api, producer)
    clock.advance(timedelta(minutes=11))

    response = await contact(api, producer, auction["id"])

    assert error_detail(response).reason == common_pb2.ERROR_REASON_AUCTION_HAS_NO_WINNER


async def test_contacts_require_a_session(api: httpx.AsyncClient) -> None:
    response = await contact(api, "", "00000000-0000-0000-0000-000000000000")

    assert response.status_code == 401
