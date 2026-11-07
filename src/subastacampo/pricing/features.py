"""Pricing features shared by training (ml/) and serving (the catalog service).

A single definition prevents training/serving skew: the model always receives
exactly the columns, units and categories it was trained with.
"""

from dataclasses import dataclass
from datetime import date

import pandas as pd

from subastacampo.db.models import ProductCategory, QuantityUnit

KG_PER_UNIT = {
    QuantityUnit.KILOGRAM: 1.0,
    QuantityUnit.ARROBA: 12.5,
    QuantityUnit.SACK: 50.0,
    QuantityUnit.CRATE: 20.0,
}

CATEGORIES = [category.value for category in ProductCategory]
CATEGORICAL_FEATURES = ["category"]
NUMERIC_FEATURES = [
    "quantity_kg",
    "remaining_days",
    "remaining_fraction",
    "month",
    "category_demand",
]
FEATURE_COLUMNS = CATEGORICAL_FEATURES + NUMERIC_FEATURES

# Domain knowledge the model must respect: 1 = price per kg never decreases when
# the feature grows, -1 = never increases. Unlisted features are unconstrained.
MONOTONIC_CONSTRAINTS = {
    "quantity_kg": -1,
    "remaining_fraction": 1,
    "category_demand": 1,
}

# Used when a category has no auctions in the demand window yet.
DEFAULT_CATEGORY_DEMAND = 3.0


@dataclass(frozen=True, slots=True)
class PriceFeatures:
    category: ProductCategory
    quantity_kg: float
    remaining_days: int
    remaining_fraction: float
    month: int
    category_demand: float


def build_features(
    *,
    category: ProductCategory,
    quantity: float,
    unit: QuantityUnit,
    harvest_date: date,
    shelf_life_days: int,
    today: date,
    category_demand: float,
) -> PriceFeatures:
    elapsed_days = (today - harvest_date).days
    remaining_days = max(0, shelf_life_days - elapsed_days)
    return PriceFeatures(
        category=category,
        quantity_kg=quantity * KG_PER_UNIT[unit],
        remaining_days=remaining_days,
        remaining_fraction=min(1.0, remaining_days / shelf_life_days),
        month=today.month,
        category_demand=category_demand,
    )


def to_frame(rows: list[PriceFeatures]) -> pd.DataFrame:
    """Builds the exact table layout the model was trained on."""
    frame = pd.DataFrame(
        {
            "category": [row.category.value for row in rows],
            "quantity_kg": [row.quantity_kg for row in rows],
            "remaining_days": [row.remaining_days for row in rows],
            "remaining_fraction": [row.remaining_fraction for row in rows],
            "month": [row.month for row in rows],
            "category_demand": [row.category_demand for row in rows],
        }
    )
    # A fixed category list keeps the encoding identical in training and serving.
    frame["category"] = pd.Categorical(frame["category"], categories=CATEGORIES)
    return frame[FEATURE_COLUMNS]
