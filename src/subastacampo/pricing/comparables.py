"""Closed auctions shown next to a suggestion, so the producer sees what it rests on.

The model's number is an estimate; a few real sales of the same category are
evidence a producer can judge for themselves. The closest in volume come first,
because price per kilo falls as lots grow.
"""

import math
from dataclasses import dataclass
from datetime import datetime

MAX_COMPARABLES = 3


@dataclass(frozen=True, slots=True)
class Sale:
    product_name: str
    quantity_kg: float
    final_price: int
    closed_at: datetime

    @property
    def price_per_kg(self) -> float:
        return self.final_price / self.quantity_kg


def closest_sales(
    sales: list[Sale], quantity_kg: float, limit: int = MAX_COMPARABLES
) -> list[Sale]:
    """Sales ranked by how far their volume is from this lot, on a log scale.

    A 10 kg lot is as far from 100 kg as 100 kg is from 1000 kg. Ties go to the
    most recent sale.
    """
    usable = [sale for sale in sales if sale.quantity_kg > 0]
    return sorted(
        usable,
        key=lambda sale: (
            round(abs(math.log(sale.quantity_kg) - math.log(quantity_kg)), 6),
            -sale.closed_at.timestamp(),
        ),
    )[:limit]
