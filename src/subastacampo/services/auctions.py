"""Auction creation, listing, bidding and live streaming."""

import uuid
from collections.abc import AsyncIterator, Callable

from connectrpc.code import Code
from connectrpc.errors import ConnectError
from connectrpc.request import RequestContext
from sqlalchemy import func, select, tuple_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from subastacampo.auctions.broker import AuctionEventBroker, SubscriptionOverflowError
from subastacampo.auctions.closing import close_if_due, load_auction
from subastacampo.auctions.events import (
    auction_closed_event,
    bid_placed_event,
    redact_for,
    snapshot_event,
)
from subastacampo.auctions.rules import (
    MAX_DURATION,
    MIN_DURATION,
    apply_anti_sniping,
    has_expired,
    minimum_next_bid,
)
from subastacampo.auth.principal import optional_principal, require_principal
from subastacampo.calendar import BusinessCalendar
from subastacampo.clock import Clock
from subastacampo.db.models import Auction, AuctionStatus, Bid, Product, UserRole
from subastacampo.errors import rpc_error, validation_error
from subastacampo.mappers import (
    STATUS_FROM_PROTO,
    money,
    money_units,
    remaining_shelf_life_days,
    to_auction_message,
    to_bid_message,
)
from subastacampo.pagination import Cursor, decode_cursor, encode_cursor, resolve_page_size
from subastacampo.v1 import auction_pb2, common_pb2
from subastacampo.v1.auction_connect import AuctionService
from subastacampo.v1.auction_pb2 import (
    CreateAuctionRequest,
    CreateAuctionResponse,
    GetAuctionRequest,
    GetAuctionResponse,
    ListAuctionsRequest,
    ListAuctionsResponse,
    PlaceBidRequest,
    PlaceBidResponse,
    WatchAuctionRequest,
    WatchAuctionResponse,
)

MAX_PRICE = 1_000_000_000_000


class AuctionServiceImpl(AuctionService):
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        calendar: BusinessCalendar,
        clock: Clock,
        broker: AuctionEventBroker,
    ) -> None:
        self._session_factory = session_factory
        self._calendar = calendar
        self._clock = clock
        self._broker = broker

    async def create_auction(
        self,
        request: CreateAuctionRequest,
        ctx: RequestContext[CreateAuctionRequest, CreateAuctionResponse],
    ) -> CreateAuctionResponse:
        principal = require_principal(role=UserRole.PRODUCER)
        product_id = _parse_id(request.product_id, _product_not_found)
        starting_price = _positive_amount(request.starting_price, "starting_price")
        min_increment = _positive_amount(request.min_increment, "min_increment")
        duration = request.duration.ToTimedelta()
        if not MIN_DURATION <= duration <= MAX_DURATION:
            raise validation_error("duration", "Duration must be between 1 minute and 24 hours")

        now = self._clock.now()
        today = self._calendar.today()
        async with self._session_factory() as session:
            product = await session.get(Product, product_id)
            if product is None:
                raise _product_not_found()
            if product.producer_id != principal.user_id:
                raise rpc_error(
                    Code.PERMISSION_DENIED,
                    common_pb2.ERROR_REASON_NOT_PRODUCT_OWNER,
                    "Only the product's producer can auction it",
                )
            if remaining_shelf_life_days(product, today=today) == 0:
                raise validation_error("product_id", "The product's shelf life has ended")

            auction = Auction(
                product=product,
                starting_price=starting_price,
                min_increment=min_increment,
                current_price=starting_price,
                starts_at=now,
                ends_at=now + duration,
                status=AuctionStatus.ACTIVE,
                extension_count=0,
                bid_count=0,
            )
            session.add(auction)
            try:
                await session.commit()
            except IntegrityError as error:
                # The partial unique index allows a single active auction per product.
                raise rpc_error(
                    Code.FAILED_PRECONDITION,
                    common_pb2.ERROR_REASON_AUCTION_ALREADY_ACTIVE,
                    "This product already has an active auction",
                ) from error

        return CreateAuctionResponse(auction=to_auction_message(auction, today=today))

    async def get_auction(
        self,
        request: GetAuctionRequest,
        ctx: RequestContext[GetAuctionRequest, GetAuctionResponse],
    ) -> GetAuctionResponse:
        auction_id = _parse_id(request.auction_id, _auction_not_found)
        async with self._session_factory() as session:
            auction, bids = await self._load_detail(session, auction_id)

        include_risk = _is_owner(auction)
        return GetAuctionResponse(
            auction=to_auction_message(auction, today=self._calendar.today()),
            bids=[to_bid_message(bid, include_risk=include_risk) for bid in bids],
        )

    async def list_auctions(
        self,
        request: ListAuctionsRequest,
        ctx: RequestContext[ListAuctionsRequest, ListAuctionsResponse],
    ) -> ListAuctionsResponse:
        size = resolve_page_size(request.page_size)
        cursor = decode_cursor(request.page_token)

        query = (
            select(Auction)
            .options(selectinload(Auction.product))
            .order_by(Auction.created_at.desc(), Auction.id.desc())
            .limit(size + 1)
        )
        if request.status != auction_pb2.AUCTION_STATUS_UNSPECIFIED:
            status = STATUS_FROM_PROTO.get(request.status)
            if status is None:
                raise validation_error("status", "Unknown auction status")
            query = query.where(Auction.status == status)
        if request.scope == auction_pb2.AUCTION_SCOPE_MY_PRODUCTS:
            principal = require_principal(role=UserRole.PRODUCER)
            query = query.join(Product, Auction.product_id == Product.id).where(
                Product.producer_id == principal.user_id
            )
        elif request.scope == auction_pb2.AUCTION_SCOPE_MY_BIDS:
            principal = require_principal(role=UserRole.BUYER)
            query = query.where(
                Auction.id.in_(select(Bid.auction_id).where(Bid.bidder_id == principal.user_id))
            )
        if cursor is not None:
            query = query.where(
                tuple_(Auction.created_at, Auction.id) < (cursor.created_at, cursor.id)
            )

        async with self._session_factory() as session:
            auctions = list((await session.scalars(query)).all())

        next_page_token = ""
        if len(auctions) > size:
            auctions = auctions[:size]
            last = auctions[-1]
            next_page_token = encode_cursor(Cursor(created_at=last.created_at, id=last.id))

        today = self._calendar.today()
        return ListAuctionsResponse(
            auctions=[to_auction_message(auction, today=today) for auction in auctions],
            next_page_token=next_page_token,
        )

    async def place_bid(
        self,
        request: PlaceBidRequest,
        ctx: RequestContext[PlaceBidRequest, PlaceBidResponse],
    ) -> PlaceBidResponse:
        principal = require_principal(role=UserRole.BUYER)
        auction_id = _parse_id(request.auction_id, _auction_not_found)
        amount = _positive_amount(request.amount, "amount")
        request_id = _parse_request_id(request.request_id)
        now = self._clock.now()

        async with self._session_factory() as session:
            # Idempotency: a retry of an accepted bid gets the same answer.
            retried = await self._replay(session, principal.user_id, request_id)
            if retried is not None:
                return retried

            # Row lock: concurrent bids on this auction wait here, one at a time.
            auction = await load_auction(session, auction_id, for_update=True)
            if auction is None:
                raise _auction_not_found()
            if await close_if_due(session, auction, now):
                await session.commit()
                self._broker.publish(
                    auction.id, auction_closed_event(auction, today=self._calendar.today())
                )
                raise _auction_not_active()
            if auction.status != AuctionStatus.ACTIVE:
                raise _auction_not_active()

            minimum = minimum_next_bid(
                starting_price=auction.starting_price,
                current_price=auction.current_price,
                min_increment=auction.min_increment,
                bid_count=auction.bid_count,
            )
            if amount < minimum:
                raise rpc_error(
                    Code.FAILED_PRECONDITION,
                    common_pb2.ERROR_REASON_BID_TOO_LOW,
                    "The bid is below the current minimum",
                    minimum_bid=money(minimum),
                )

            close_time = apply_anti_sniping(now=now, ends_at=auction.ends_at)
            bid = Bid(
                auction_id=auction.id,
                bidder_id=principal.user_id,
                bidder_number=await _bidder_number(session, auction.id, principal.user_id),
                amount=amount,
                placed_at=now,
                extended_close=close_time.extended,
                request_id=request_id,
                risk_score=0,
                risk_signals=[],
            )
            session.add(bid)
            auction.current_price = amount
            auction.bid_count += 1
            if close_time.extended:
                auction.ends_at = close_time.ends_at
                auction.extension_count += 1

            try:
                await session.commit()
            except IntegrityError:
                # Two retries of the same request raced and the other one won.
                await session.rollback()
                retried = await self._replay(session, principal.user_id, request_id)
                if retried is None:
                    raise
                return retried

        today = self._calendar.today()
        # Published only after the commit: nobody hears about a bid that was not saved.
        self._broker.publish(auction.id, bid_placed_event(bid, auction, today=today))
        return PlaceBidResponse(
            bid=to_bid_message(bid, include_risk=False),
            auction=to_auction_message(auction, today=today),
        )

    async def watch_auction(
        self,
        request: WatchAuctionRequest,
        ctx: RequestContext[WatchAuctionRequest, WatchAuctionResponse],
    ) -> AsyncIterator[WatchAuctionResponse]:
        auction_id = _parse_id(request.auction_id, _auction_not_found)

        # Subscribe BEFORE reading the snapshot: an event published in between is
        # queued instead of lost. Duplicates are harmless: clients dedupe by id.
        with self._broker.subscribe(auction_id) as subscription:
            async with self._session_factory() as session:
                auction, bids = await self._load_detail(session, auction_id)
            include_risk = _is_owner(auction)

            yield snapshot_event(
                auction, bids, today=self._calendar.today(), include_risk=include_risk
            )
            if auction.status != AuctionStatus.ACTIVE:
                return

            while True:
                try:
                    event = await subscription.next()
                except SubscriptionOverflowError as error:
                    raise ConnectError(
                        Code.ABORTED, "Too many pending events: reconnect to resume"
                    ) from error
                yield redact_for(event, include_risk=include_risk)
                if event.WhichOneof("event") == "auction_closed":
                    return

    async def _load_detail(
        self, session: AsyncSession, auction_id: uuid.UUID
    ) -> tuple[Auction, list[Bid]]:
        """Auction with its bids, newest first. Closes it lazily if its time is up."""
        now = self._clock.now()
        auction = await load_auction(session, auction_id)
        if auction is None:
            raise _auction_not_found()

        if auction.status == AuctionStatus.ACTIVE and has_expired(now=now, ends_at=auction.ends_at):
            locked = await load_auction(session, auction_id, for_update=True)
            assert locked is not None
            auction = locked
            if await close_if_due(session, auction, now):
                await session.commit()
                self._broker.publish(
                    auction.id, auction_closed_event(auction, today=self._calendar.today())
                )

        bids = (
            await session.scalars(
                select(Bid)
                .where(Bid.auction_id == auction.id)
                .order_by(Bid.placed_at.desc(), Bid.id.desc())
            )
        ).all()
        return auction, list(bids)

    async def _replay(
        self, session: AsyncSession, bidder_id: uuid.UUID, request_id: uuid.UUID
    ) -> PlaceBidResponse | None:
        bid = await session.scalar(
            select(Bid).where(Bid.bidder_id == bidder_id, Bid.request_id == request_id)
        )
        if bid is None:
            return None
        auction = await load_auction(session, bid.auction_id)
        assert auction is not None
        return PlaceBidResponse(
            bid=to_bid_message(bid, include_risk=False),
            auction=to_auction_message(auction, today=self._calendar.today()),
        )


async def _bidder_number(session: AsyncSession, auction_id: uuid.UUID, bidder_id: uuid.UUID) -> int:
    """Same anonymous number for every bid of a person in an auction.

    Safe without extra locking: the caller holds the auction's row lock.
    """
    existing = await session.scalar(
        select(Bid.bidder_number)
        .where(Bid.auction_id == auction_id, Bid.bidder_id == bidder_id)
        .limit(1)
    )
    if existing is not None:
        return existing
    highest = await session.scalar(
        select(func.coalesce(func.max(Bid.bidder_number), 0)).where(Bid.auction_id == auction_id)
    )
    return int(highest or 0) + 1


def _is_owner(auction: Auction) -> bool:
    principal = optional_principal()
    return principal is not None and principal.user_id == auction.product.producer_id


def _positive_amount(value: common_pb2.Money, field: str) -> int:
    units = money_units(value, field)
    if not 0 < units <= MAX_PRICE:
        raise validation_error(field, "Amount must be positive")
    return units


def _parse_request_id(raw: str) -> uuid.UUID:
    try:
        return uuid.UUID(raw)
    except ValueError as error:
        raise validation_error("request_id", "request_id must be a UUID") from error


def _parse_id(raw: str, not_found: Callable[[], ConnectError]) -> uuid.UUID:
    """A malformed id is reported as not found: it reveals nothing."""
    try:
        return uuid.UUID(raw)
    except ValueError as error:
        raise not_found() from error


def _product_not_found() -> ConnectError:
    return rpc_error(Code.NOT_FOUND, common_pb2.ERROR_REASON_PRODUCT_NOT_FOUND, "Product not found")


def _auction_not_found() -> ConnectError:
    return rpc_error(Code.NOT_FOUND, common_pb2.ERROR_REASON_AUCTION_NOT_FOUND, "Auction not found")


def _auction_not_active() -> ConnectError:
    return rpc_error(
        Code.FAILED_PRECONDITION,
        common_pb2.ERROR_REASON_AUCTION_NOT_ACTIVE,
        "The auction is not active",
    )
