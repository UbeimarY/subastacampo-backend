"""Price per kilo the platform's own sales reached, by category and week."""

import statistics
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta

from subastacampo.db.models import ProductCategory
from subastacampo.pricing.comparables import Sale

MIN_DAYS, DEFAULT_DAYS, MAX_DAYS = 7, 60, 180


@dataclass(frozen=True, slots=True)
class Week:
    start: date
    sales: int
    median_price_per_kg: float


@dataclass(frozen=True, slots=True)
class CategorySummary:
    category: ProductCategory
    sales: int
    median_price_per_kg: float
    lowest_price_per_kg: float
    highest_price_per_kg: float
    weeks: list[Week]


def period_days(requested: int) -> int:
    """Unset means the default; anything else is held between the bounds."""
    if requested == 0:
        return DEFAULT_DAYS
    return max(MIN_DAYS, min(MAX_DAYS, requested))


def summarize(sales_by_category: dict[ProductCategory, list[Sale]]) -> list[CategorySummary]:
    """Medians, not means: one unusual lot should not move the market price."""
    summaries = []
    for category, sales in sales_by_category.items():
        if not sales:
            continue
        prices = [sale.price_per_kg for sale in sales]
        by_week: dict[date, list[float]] = defaultdict(list)
        for sale in sales:
            day = sale.closed_at.date()
            by_week[day - timedelta(days=day.weekday())].append(sale.price_per_kg)
        summaries.append(
            CategorySummary(
                category=category,
                sales=len(sales),
                median_price_per_kg=statistics.median(prices),
                lowest_price_per_kg=min(prices),
                highest_price_per_kg=max(prices),
                weeks=[
                    Week(start, len(values), statistics.median(values))
                    for start, values in sorted(by_week.items())
                ],
            )
        )
    return sorted(summaries, key=lambda summary: -summary.sales)
