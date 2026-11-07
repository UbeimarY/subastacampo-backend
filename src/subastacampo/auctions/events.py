"""Live auction events sent over WatchAuction."""

from datetime import date

from subastacampo.db.models import Auction, Bid
from subastacampo.mappers import to_auction_message, to_bid_message
from subastacampo.v1.auction_pb2 import (
    AuctionClosed,
    AuctionSnapshot,
    BidPlaced,
    WatchAuctionResponse,
)


def snapshot_event(
    auction: Auction, bids: list[Bid], *, today: date, include_risk: bool
) -> WatchAuctionResponse:
    return WatchAuctionResponse(
        snapshot=AuctionSnapshot(
            auction=to_auction_message(auction, today=today),
            bids=[to_bid_message(bid, include_risk=include_risk) for bid in bids],
        )
    )


def bid_placed_event(bid: Bid, auction: Auction, *, today: date) -> WatchAuctionResponse:
    """Built with the risk included; redact_for() removes it for each viewer."""
    return WatchAuctionResponse(
        bid_placed=BidPlaced(
            bid=to_bid_message(bid, include_risk=True),
            auction=to_auction_message(auction, today=today),
        )
    )


def auction_closed_event(auction: Auction, *, today: date) -> WatchAuctionResponse:
    return WatchAuctionResponse(
        auction_closed=AuctionClosed(auction=to_auction_message(auction, today=today))
    )


def redact_for(event: WatchAuctionResponse, *, include_risk: bool) -> WatchAuctionResponse:
    """The same event object is shared by every subscriber: never mutate it."""
    if include_risk or event.WhichOneof("event") != "bid_placed":
        return event
    redacted = WatchAuctionResponse()
    redacted.CopyFrom(event)
    redacted.bid_placed.bid.ClearField("risk")
    return redacted

    