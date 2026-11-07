"""Turns the model inputs into the factors shown to the producer.

The number comes from the model; these rules explain it in plain terms. The
monotonic constraints guarantee both always point in the same direction.
"""

import enum
from dataclasses import dataclass

from subastacampo.pricing.features import PriceFeatures

FRESH_FRACTION = 0.8
SHORT_SHELF_LIFE_FRACTION = 0.3
HIGH_DEMAND = 5.0
LOW_DEMAND = 2.0
LARGE_VOLUME_KG = 500.0


class FactorCode(enum.Enum):
    FRESH_PRODUCT = "fresh_product"
    SHORT_SHELF_LIFE = "short_shelf_life"
    HIGH_DEMAND = "high_demand"
    LOW_DEMAND = "low_demand"
    LARGE_VOLUME = "large_volume"


@dataclass(frozen=True, slots=True)
class Factor:
    code: FactorCode
    value: float


def explain(features: PriceFeatures, *, has_demand_data: bool) -> list[Factor]:
    factors: list[Factor] = []

    if features.remaining_fraction > FRESH_FRACTION:
        factors.append(Factor(FactorCode.FRESH_PRODUCT, features.remaining_days))
    elif features.remaining_fraction < SHORT_SHELF_LIFE_FRACTION:
        factors.append(Factor(FactorCode.SHORT_SHELF_LIFE, features.remaining_days))

    # Without real auctions in the window, demand is a neutral default: no claim.
    if has_demand_data:
        if features.category_demand >= HIGH_DEMAND:
            factors.append(Factor(FactorCode.HIGH_DEMAND, features.category_demand))
        elif features.category_demand <= LOW_DEMAND:
            factors.append(Factor(FactorCode.LOW_DEMAND, features.category_demand))

    if features.quantity_kg >= LARGE_VOLUME_KG:
        factors.append(Factor(FactorCode.LARGE_VOLUME, features.quantity_kg))

    return factors
