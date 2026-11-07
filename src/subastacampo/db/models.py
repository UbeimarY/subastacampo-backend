"""Database models.

Money is stored in whole Colombian pesos (BIGINT), matching the int64 Money
message of the contract. Business invariants are enforced by the database.
"""

import enum
import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    false,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column, relationship

from subastacampo.db.base import Base


class UserRole(enum.StrEnum):
    PRODUCER = "producer"
    BUYER = "buyer"


class ProductCategory(enum.StrEnum):
    FRUITS = "fruits"
    VEGETABLES = "vegetables"
    TUBERS = "tubers"
    GRAINS = "grains"
    DAIRY = "dairy"
    OTHER = "other"


class QuantityUnit(enum.StrEnum):
    KILOGRAM = "kilogram"
    ARROBA = "arroba"
    SACK = "sack"
    CRATE = "crate"


class AuctionStatus(enum.StrEnum):
    SCHEDULED = "scheduled"
    ACTIVE = "active"
    CLOSED = "closed"
    CANCELLED = "cancelled"


def _enum_column(enum_class: type[enum.StrEnum], name: str) -> Enum:
    """VARCHAR + CHECK constraint instead of a native PostgreSQL ENUM.

    Adding a value later becomes a regular migration instead of an ALTER TYPE.
    """
    return Enum(
        enum_class,
        name=name,
        native_enum=False,
        create_constraint=True,
        length=20,
        values_callable=lambda members: [member.value for member in members],
        validate_strings=True,
    )


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    full_name: Mapped[str] = mapped_column(String(100))
    # Always stored lowercase, so lookups are case-insensitive.
    email: Mapped[str] = mapped_column(String(255), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[UserRole] = mapped_column(_enum_column(UserRole, "user_role"))
    phone: Mapped[str | None] = mapped_column(String(20))
    municipality: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    products: Mapped[list["Product"]] = relationship(back_populates="producer", lazy="raise")


class Product(Base):
    __tablename__ = "products"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="quantity_positive"),
        CheckConstraint("shelf_life_days BETWEEN 1 AND 365", name="shelf_life_range"),
        Index("ix_products_category_created_at", "category", "created_at"),
        # Newest-first listing with keyset pagination (created_at, id).
        Index("ix_products_created_at_id", "created_at", "id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    producer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(150))
    description: Mapped[str] = mapped_column(Text, default="", server_default="")
    category: Mapped[ProductCategory] = mapped_column(
        _enum_column(ProductCategory, "product_category")
    )
    quantity: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    unit: Mapped[QuantityUnit] = mapped_column(_enum_column(QuantityUnit, "quantity_unit"))
    harvest_date: Mapped[date]
    shelf_life_days: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    producer: Mapped[User] = relationship(back_populates="products", lazy="raise")
    auctions: Mapped[list["Auction"]] = relationship(back_populates="product", lazy="raise")


class Auction(Base):
    __tablename__ = "auctions"
    __table_args__ = (
        CheckConstraint("starting_price > 0", name="starting_price_positive"),
        CheckConstraint("min_increment > 0", name="min_increment_positive"),
        CheckConstraint("current_price >= starting_price", name="current_price_not_below_start"),
        CheckConstraint("ends_at > starts_at", name="ends_after_start"),
        # At most one ACTIVE auction per product, guaranteed by the database itself.
        # Past auctions of the same product are still allowed (partial index).
        Index(
            "uq_auctions_one_active_per_product",
            "product_id",
            unique=True,
            postgresql_where=text("status = 'active'"),
        ),
        # Used by the background task that closes expired auctions.
        Index("ix_auctions_status_ends_at", "status", "ends_at"),
        # Newest-first listing with keyset pagination (created_at, id).
        Index("ix_auctions_created_at_id", "created_at", "id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("products.id"), index=True)
    starting_price: Mapped[int] = mapped_column(BigInteger)
    min_increment: Mapped[int] = mapped_column(BigInteger)
    current_price: Mapped[int] = mapped_column(BigInteger)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[AuctionStatus] = mapped_column(
        _enum_column(AuctionStatus, "auction_status"), default=AuctionStatus.ACTIVE
    )
    extension_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    bid_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    winner_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    product: Mapped[Product] = relationship(back_populates="auctions", lazy="raise")
    bids: Mapped[list["Bid"]] = relationship(back_populates="auction", lazy="raise")


class Bid(Base):
    __tablename__ = "bids"
    __table_args__ = (
        CheckConstraint("amount > 0", name="amount_positive"),
        CheckConstraint("risk_score BETWEEN 0 AND 100", name="risk_score_range"),
        # Idempotency: retrying a request with the same id never creates a second bid.
        UniqueConstraint("bidder_id", "request_id"),
        Index("ix_bids_auction_id_placed_at", "auction_id", "placed_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    auction_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("auctions.id"))
    bidder_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    # Anonymous number of the bidder within this auction ("Comprador #3").
    bidder_number: Mapped[int] = mapped_column(Integer)
    amount: Mapped[int] = mapped_column(BigInteger)
    placed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # True when this bid pushed the closing time (anti-sniping rule).
    extended_close: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    request_id: Mapped[uuid.UUID]
    risk_score: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    # RiskSignal names from the contract, for example "LAST_10_SECONDS".
    risk_signals: Mapped[list[str]] = mapped_column(
        ARRAY(String(40)), default=list, server_default=text("'{}'")
    )

    auction: Mapped[Auction] = relationship(back_populates="bids", lazy="raise")
