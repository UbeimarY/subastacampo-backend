"""Reproducible synthetic auction dataset for the initial pricing model.

There is no real auction history yet. The generator encodes reference prices
and market effects (freshness, season, demand and volume); the model has to
learn them from examples alone. It will be replaced by real data: SIPSA-DANE
reference prices and the platform's closed auctions.
"""

import numpy as np
import pandas as pd

from subastacampo.db.models import ProductCategory, QuantityUnit
from subastacampo.pricing.features import CATEGORIES, FEATURE_COLUMNS, KG_PER_UNIT

TARGET = "price_per_kg"

# Approximate reference prices in COP per kg, to be calibrated with SIPSA-DANE.
BASE_PRICE_PER_KG = {
    ProductCategory.FRUITS: 3500,
    ProductCategory.VEGETABLES: 2500,
    ProductCategory.TUBERS: 1800,
    ProductCategory.GRAINS: 4500,
    ProductCategory.DAIRY: 5000,
    ProductCategory.OTHER: 3000,
}
SHELF_LIFE_RANGE = {
    ProductCategory.FRUITS: (5, 20),
    ProductCategory.VEGETABLES: (5, 15),
    ProductCategory.TUBERS: (20, 60),
    ProductCategory.GRAINS: (90, 365),
    ProductCategory.DAIRY: (3, 15),
    ProductCategory.OTHER: (7, 60),
}
# Month shift of each category's harvest peak.
SEASON_OFFSET = {
    ProductCategory.FRUITS: 0,
    ProductCategory.VEGETABLES: 3,
    ProductCategory.TUBERS: 6,
    ProductCategory.GRAINS: 9,
    ProductCategory.DAIRY: 2,
    ProductCategory.OTHER: 5,
}


def generate(rows: int = 8000, seed: int = 42) -> pd.DataFrame:
    """Same seed, same dataset: training is fully reproducible."""
    rng = np.random.default_rng(seed)
    categories = list(ProductCategory)
    units = list(QuantityUnit)
    records = []

    for _ in range(rows):
        category = categories[rng.integers(len(categories))]
        low, high = SHELF_LIFE_RANGE[category]
        shelf_life = int(rng.integers(low, high + 1))
        remaining = int(rng.integers(0, shelf_life + 1))
        fraction = remaining / shelf_life
        unit = units[rng.integers(len(units))]
        quantity_kg = float(rng.integers(1, 60)) * KG_PER_UNIT[unit]
        month = int(rng.integers(1, 13))
        demand = float(rng.gamma(2.0, 2.0))

        season = 1 + 0.15 * np.sin(2 * np.pi * (month + SEASON_OFFSET[category]) / 12)
        demand_effect = min(1.25, 0.85 + 0.05 * demand)
        freshness = 0.55 + 0.45 * fraction**0.7
        volume = 1 - 0.06 * np.log10(quantity_kg)
        noise = rng.lognormal(0, 0.08)
        price = BASE_PRICE_PER_KG[category] * season * demand_effect * freshness * volume * noise

        records.append(
            {
                "category": category.value,
                "quantity_kg": quantity_kg,
                "remaining_days": remaining,
                "remaining_fraction": fraction,
                "month": month,
                "category_demand": demand,
                TARGET: price,
            }
        )

    frame = pd.DataFrame.from_records(records)
    frame["category"] = pd.Categorical(frame["category"], categories=CATEGORIES)
    return frame[[*FEATURE_COLUMNS, TARGET]]
