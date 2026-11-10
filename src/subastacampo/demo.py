"""Fills an empty database with a believable market, for demos and the defense.

    DEMO_PASSWORD=... uv run python -m subastacampo.demo

Creates producers and buyers from Nariño, six weeks of closed auctions (they feed
the market prices and the price suggestion's comparable sales) and live auctions
closing over the next hours, some with an automatic bid already set. Every
account uses DEMO_PASSWORD. Runs once: it stops if the demo accounts exist.
"""

import asyncio
import os
import secrets
import sys
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import TypeVar

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from subastacampo.auth.passwords import hash_password
from subastacampo.config import get_settings
from subastacampo.db.models import (
    Auction,
    AuctionStatus,
    AutoBid,
    Bid,
    Product,
    ProductCategory,
    QuantityUnit,
    User,
    UserRole,
)
from subastacampo.pricing.features import KG_PER_UNIT

DOMAIN = "demo.subastacampo.co"
T = TypeVar("T")


class Dice:
    """Randomness for the demo, drawn from the `secrets` module only.

    Demo data does not need security, but a codebase with no predictable
    generator anywhere is simpler to audit than one with justified exceptions.
    Every run produces a different, believable market.
    """

    def random(self) -> float:
        return secrets.randbelow(1_000_000) / 1_000_000

    def randint(self, low: int, high: int) -> int:
        return low + secrets.randbelow(high - low + 1)

    def uniform(self, low: float, high: float) -> float:
        return low + (high - low) * self.random()

    def choice(self, options: Sequence[T]) -> T:
        return secrets.choice(options)

    def sample(self, options: Sequence[T], k: int) -> list[T]:
        pool = list(options)
        for i in range(len(pool) - 1, 0, -1):  # Fisher-Yates
            j = secrets.randbelow(i + 1)
            pool[i], pool[j] = pool[j], pool[i]
        return pool[:k]


@dataclass(frozen=True)
class Lot:
    name: str
    description: str
    category: ProductCategory
    unit: QuantityUnit
    quantity: tuple[int, int]
    shelf_life_days: int
    price_per_kg: int  # typical COP per kg


LOTS = [
    Lot(
        "Papa pastusa",
        "Papa pastusa de primera, lavada.",
        ProductCategory.TUBERS,
        QuantityUnit.SACK,
        (5, 40),
        30,
        1700,
    ),
    Lot(
        "Papa amarilla",
        "Papa criolla amarilla, cosecha de la semana.",
        ProductCategory.TUBERS,
        QuantityUnit.ARROBA,
        (4, 30),
        20,
        2600,
    ),
    Lot(
        "Ulloco",
        "Ulloco de altura, seleccionado.",
        ProductCategory.TUBERS,
        QuantityUnit.ARROBA,
        (2, 12),
        25,
        2900,
    ),
    Lot(
        "Mora de Castilla",
        "Mora madura en canastilla.",
        ProductCategory.FRUITS,
        QuantityUnit.CRATE,
        (3, 20),
        6,
        3800,
    ),
    Lot(
        "Lulo",
        "Lulo pintón, buen calibre.",
        ProductCategory.FRUITS,
        QuantityUnit.CRATE,
        (2, 15),
        10,
        3300,
    ),
    Lot(
        "Uchuva",
        "Uchuva con capacho, tipo exportación.",
        ProductCategory.FRUITS,
        QuantityUnit.KILOGRAM,
        (20, 200),
        15,
        6200,
    ),
    Lot(
        "Arveja verde",
        "Arveja en vaina, recién cosechada.",
        ProductCategory.VEGETABLES,
        QuantityUnit.ARROBA,
        (3, 25),
        7,
        3200,
    ),
    Lot(
        "Zanahoria",
        "Zanahoria lavada, calibre parejo.",
        ProductCategory.VEGETABLES,
        QuantityUnit.SACK,
        (2, 20),
        20,
        1500,
    ),
    Lot(
        "Haba seca",
        "Haba seca seleccionada, buena para semilla.",
        ProductCategory.GRAINS,
        QuantityUnit.SACK,
        (2, 15),
        120,
        5200,
    ),
    Lot(
        "Quinua",
        "Quinua lavada y desaponificada.",
        ProductCategory.GRAINS,
        QuantityUnit.KILOGRAM,
        (25, 300),
        180,
        9000,
    ),
    Lot(
        "Queso campesino",
        "Queso fresco de vereda, en bloques.",
        ProductCategory.DAIRY,
        QuantityUnit.KILOGRAM,
        (10, 80),
        8,
        14000,
    ),
    Lot(
        "Leche cruda",
        "Leche del ordeño de la mañana, en cantinas.",
        ProductCategory.DAIRY,
        QuantityUnit.KILOGRAM,
        (40, 400),
        2,
        1900,
    ),
]

PRODUCERS = [
    ("Rosa Pantoja", "Pasto", "3101112233"),
    ("Jesús Erazo", "Ipiales", "3102223344"),
    ("Marleny Chamorro", "Túquerres", "3103334455"),
    ("Aurelio Benavides", "La Cruz", "3104445566"),
]
BUYERS = [
    ("Fruver La 18", "Pasto", "3205556677"),
    ("Restaurante El Encano", "Pasto", "3206667788"),
    ("Distribuidora del Sur", "Ipiales", "3207778899"),
    ("Tienda Doña Carmen", "Sandoná", "3208889900"),
]


async def seed(session: AsyncSession, password: str, now: datetime) -> dict[str, int]:
    rng = Dice()
    password_hash = await hash_password(password)
    joined = now - timedelta(days=90)

    def account(i: int, kind: str, role: UserRole, row: tuple[str, str, str]) -> User:
        name, town, phone = row
        return User(
            full_name=name,
            email=f"{kind}{i}@{DOMAIN}",
            password_hash=password_hash,
            role=role,
            phone=phone,
            municipality=town,
            created_at=joined,
        )

    producers = [account(i, "productor", UserRole.PRODUCER, r) for i, r in enumerate(PRODUCERS, 1)]
    buyers = [account(i, "comprador", UserRole.BUYER, r) for i, r in enumerate(BUYERS, 1)]
    session.add_all([*producers, *buyers])
    await session.flush()

    counts = {"closed": 0, "live": 0, "bids": 0}

    def make_lot(lot: Lot, producer: User, created: datetime) -> tuple[Product, float]:
        low, high = lot.quantity
        quantity = rng.randint(low, high)
        product = Product(
            producer_id=producer.id,
            name=lot.name,
            description=lot.description,
            category=lot.category,
            quantity=Decimal(quantity),
            unit=lot.unit,
            harvest_date=(created - timedelta(days=rng.randint(0, 2))).date(),
            shelf_life_days=lot.shelf_life_days,
            origin=producer.municipality,
            created_at=created,
        )
        session.add(product)
        return product, quantity * KG_PER_UNIT[lot.unit]

    def add_bids(auction: Auction, start: datetime, end: datetime, steps: int) -> None:
        numbers: dict[uuid.UUID, int] = {}
        price = auction.starting_price
        bidders = rng.sample(buyers, k=min(len(buyers), rng.randint(2, 4)))
        last: User | None = None
        for step in range(steps):
            bidder = rng.choice([b for b in bidders if b is not last])
            if step > 0:
                price += auction.min_increment * rng.randint(1, 3)
            numbers.setdefault(bidder.id, len(numbers) + 1)
            placed = start + (end - start) * (step + 1) / (steps + 1)
            session.add(
                Bid(
                    auction_id=auction.id,
                    bidder_id=bidder.id,
                    bidder_number=numbers[bidder.id],
                    amount=price,
                    placed_at=placed,
                    request_id=uuid.uuid4(),
                )
            )
            last = bidder
            counts["bids"] += 1
        auction.current_price = price
        auction.bid_count = steps
        auction.winner_id = last.id if last and auction.status == AuctionStatus.CLOSED else None

    def price_for(lot: Lot, kg: float) -> tuple[int, int]:
        per_kg = lot.price_per_kg * rng.uniform(0.82, 1.05) * (1 - 0.04 * (kg > 500))
        start = max(10_000, round(per_kg * kg * 0.85 / 1000) * 1000)
        increment = max(1_000, round(start * 0.02 / 1000) * 1000)
        return start, increment

    # Six weeks of history.
    for _ in range(42):
        lot = rng.choice(LOTS)
        ends = now - timedelta(days=rng.uniform(0.5, 42))
        starts = ends - timedelta(hours=rng.choice([2, 4, 6, 12]))
        product, kg = make_lot(lot, rng.choice(producers), starts - timedelta(hours=1))
        await session.flush()
        start_price, increment = price_for(lot, kg)
        auction = Auction(
            product_id=product.id,
            starting_price=start_price,
            min_increment=increment,
            current_price=start_price,
            starts_at=starts,
            ends_at=ends,
            status=AuctionStatus.CLOSED,
            created_at=starts,
        )
        session.add(auction)
        await session.flush()
        # One in seven closes without bids, as real ones do.
        if rng.random() > 1 / 7:
            add_bids(auction, starts, ends, rng.randint(2, 8))
        counts["closed"] += 1

    # Live now, closing over the next hours.
    live: list[Auction] = []
    for minutes in (25, 55, 90, 140, 200, 260, 330, 420):
        lot = rng.choice(LOTS)
        starts = now - timedelta(minutes=rng.randint(20, 120))
        product, kg = make_lot(lot, rng.choice(producers), starts - timedelta(minutes=10))
        await session.flush()
        start_price, increment = price_for(lot, kg)
        auction = Auction(
            product_id=product.id,
            starting_price=start_price,
            min_increment=increment,
            current_price=start_price,
            starts_at=starts,
            ends_at=now + timedelta(minutes=minutes),
            status=AuctionStatus.ACTIVE,
            created_at=starts,
        )
        session.add(auction)
        await session.flush()
        if rng.random() < 0.75:
            add_bids(auction, starts, now, rng.randint(1, 6))
        live.append(auction)
        counts["live"] += 1

    # An automatic bid already waiting, so the demo shows it answering.
    target = live[2]
    session.add(
        AutoBid(
            auction_id=target.id,
            bidder_id=buyers[0].id,
            max_amount=target.current_price + 6 * target.min_increment,
            created_at=now,
            updated_at=now,
        )
    )
    return counts


async def main() -> int:
    password = os.environ.get("DEMO_PASSWORD", "")
    if len(password) < 8:
        print("Set DEMO_PASSWORD (8 characters or more) for the demo accounts.", file=sys.stderr)
        return 2
    engine = create_async_engine(get_settings().database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessions() as session:
            if await session.scalar(select(User.id).where(User.email.endswith(f"@{DOMAIN}"))):
                print("The demo data already exists; nothing to do.")
                return 0
            counts = await seed(session, password, datetime.now(UTC))
            await session.commit()
    finally:
        await engine.dispose()
    print(
        f"Demo ready: {counts['live']} live auctions, {counts['closed']} closed, "
        f"{counts['bids']} bids."
    )
    print(f"Sign in as productor1@{DOMAIN} or comprador1@{DOMAIN} with DEMO_PASSWORD.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
