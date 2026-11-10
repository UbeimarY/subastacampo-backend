from datetime import UTC, datetime, timedelta

from subastacampo.pricing.comparables import Sale, closest_sales

NOW = datetime(2026, 11, 10, tzinfo=UTC)


def sale(kg: float, days_ago: int = 1, name: str = "Papa") -> Sale:
    return Sale(name, kg, final_price=int(kg * 1800), closed_at=NOW - timedelta(days=days_ago))


def test_the_closest_volumes_come_first_on_a_log_scale() -> None:
    sales = [sale(1000), sale(12), sale(90), sale(5000)]

    ranked = closest_sales(sales, quantity_kg=100)

    # 90 kg is nearest; 1000 kg (10x) beats 12 kg (8.3x smaller).
    assert [s.quantity_kg for s in ranked] == [90, 12, 1000]


def test_ties_go_to_the_most_recent_sale() -> None:
    ranked = closest_sales(
        [sale(100, days_ago=5, name="vieja"), sale(100, days_ago=1, name="nueva")], 100
    )

    assert [s.product_name for s in ranked] == ["nueva", "vieja"]


def test_price_per_kilo_comes_from_the_final_price() -> None:
    assert sale(50).price_per_kg == 1800
