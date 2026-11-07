"""The model must respect domain rules, not just score well on average."""

import json
from dataclasses import replace
from datetime import date

import pytest

from subastacampo.db.models import ProductCategory, QuantityUnit
from subastacampo.pricing.features import PriceFeatures, build_features
from subastacampo.pricing.model import MODEL_CARD_PATH, PricingModel

BASE = PriceFeatures(
    category=ProductCategory.TUBERS,
    quantity_kg=200.0,
    remaining_days=20,
    remaining_fraction=0.67,
    month=11,
    category_demand=3.0,
)


@pytest.fixture(scope="module")
def model() -> PricingModel:
    return PricingModel.load()


def test_quantity_is_converted_to_kilograms() -> None:
    features = build_features(
        category=ProductCategory.TUBERS,
        quantity=20,
        unit=QuantityUnit.SACK,
        harvest_date=date(2026, 11, 2),
        shelf_life_days=30,
        today=date(2026, 11, 4),
        category_demand=3.0,
    )

    assert features.quantity_kg == 1000.0
    assert features.remaining_days == 28


def test_fresher_product_never_gets_a_lower_price(model: PricingModel) -> None:
    almost_expired = model.predict(replace(BASE, remaining_days=2, remaining_fraction=0.07))
    fresh = model.predict(replace(BASE, remaining_days=28, remaining_fraction=0.93))

    assert fresh.median >= almost_expired.median


def test_more_demand_never_lowers_the_price(model: PricingModel) -> None:
    low_demand = model.predict(replace(BASE, category_demand=1.0))
    high_demand = model.predict(replace(BASE, category_demand=8.0))

    assert high_demand.median >= low_demand.median


def test_larger_volume_never_raises_the_price_per_kg(model: PricingModel) -> None:
    small = model.predict(replace(BASE, quantity_kg=50.0))
    large = model.predict(replace(BASE, quantity_kg=2000.0))

    assert large.median <= small.median


def test_range_is_ordered(model: PricingModel) -> None:
    prediction = model.predict(BASE)

    assert prediction.low <= prediction.median <= prediction.high


def test_model_beats_the_no_ai_baseline() -> None:
    card = json.loads(MODEL_CARD_PATH.read_text(encoding="utf-8"))

    assert card["metrics"]["improvement_over_baseline"] > 0.3
