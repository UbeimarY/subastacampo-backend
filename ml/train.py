"""Trains the price suggestion model and writes the artifact plus its model card.

Run from the repository root:  uv run python -m ml.train

Version 2 calibrates the range. Quantile models aim at the 10th and 90th
percentiles, but boosting rarely hits them: v1's "80 %" range held only 72 % of
the test prices. A conformal step (CQR, Romano et al. 2019) measures on data the
models never saw how far prices fall outside the range, relative to the median,
and widens it just enough to keep the promise. With exchangeable data the
coverage guarantee holds whatever the model gets wrong.
"""

import json
from datetime import UTC, datetime

import joblib
import numpy as np
import sklearn
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.metrics import mean_absolute_error, mean_absolute_percentage_error, r2_score
from sklearn.model_selection import train_test_split

from ml.dataset import TARGET, generate
from subastacampo.pricing.features import CATEGORIES, FEATURE_COLUMNS, MONOTONIC_CONSTRAINTS
from subastacampo.pricing.model import (
    ARTIFACTS_DIR,
    MODEL_CARD_PATH,
    MODEL_FORMAT_VERSION,
    MODEL_PATH,
)

SEED = 42
DATASET_ROWS = 12000
QUANTILES = {"low": 0.1, "median": 0.5, "high": 0.9}
TARGET_COVERAGE = 0.8


def conformal_margin(
    low: np.ndarray, median: np.ndarray, high: np.ndarray, actual: np.ndarray, coverage: float
) -> float:
    """Widening, as a fraction of the median, that keeps `coverage` of prices inside.

    The score is how far each price falls outside [low, high] relative to the median
    (negative when inside). The finite-sample quantile ceil((n+1)c)/n makes the
    coverage hold on new data, not only on the calibration set.
    """
    scores = np.maximum((low - actual) / median, (actual - high) / median)
    n = len(scores)
    level = min(1.0, np.ceil((n + 1) * coverage) / n)
    return float(np.quantile(scores, level, method="higher"))


def interval_metrics(
    low: np.ndarray, median: np.ndarray, high: np.ndarray, actual: np.ndarray
) -> dict[str, float]:
    inside = (actual >= low) & (actual <= high)
    return {
        "coverage": round(float(np.mean(inside)), 3),
        "mean_relative_width": round(float(np.mean((high - low) / median)), 3),
    }


def build_model(quantile: float) -> HistGradientBoostingRegressor:
    return HistGradientBoostingRegressor(
        loss="quantile",
        quantile=quantile,
        max_iter=400,
        learning_rate=0.05,
        max_leaf_nodes=31,
        categorical_features="from_dtype",
        monotonic_cst=MONOTONIC_CONSTRAINTS,
        random_state=SEED,
    )


def main() -> None:
    data = generate(rows=DATASET_ROWS, seed=SEED)
    features, target = data[FEATURE_COLUMNS], data[TARGET]
    # 60 % to fit, 20 % to calibrate the range, 20 % to measure: three sets that
    # never share a row, so the reported coverage is honest.
    x_fit, x_rest, y_fit, y_rest = train_test_split(
        features, target, test_size=0.4, random_state=SEED
    )
    x_cal, x_test, y_cal, y_test = train_test_split(
        x_rest, y_rest, test_size=0.5, random_state=SEED
    )

    models = {name: build_model(q).fit(x_fit, y_fit) for name, q in QUANTILES.items()}

    def predict(frame):
        low, median, high = np.sort(
            np.vstack([models[name].predict(frame) for name in ("low", "median", "high")]),
            axis=0,
        )
        return low, median, high

    cal_low, cal_median, cal_high = predict(x_cal)
    margin = conformal_margin(
        cal_low, cal_median, cal_high, y_cal.to_numpy(), coverage=TARGET_COVERAGE
    )

    low, median, high = predict(x_test)
    actual = y_test.to_numpy()
    calibrated_low = np.maximum(0.0, low - margin * median)
    calibrated_high = high + margin * median

    # Baseline: what someone would do without AI (the category's average price).
    category_mean = y_fit.groupby(x_fit["category"], observed=True).mean()
    baseline = x_test["category"].map(category_mean).astype(float)

    mae = mean_absolute_error(y_test, median)
    baseline_mae = mean_absolute_error(y_test, baseline)
    calibrated = interval_metrics(calibrated_low, median, calibrated_high, actual)
    per_category = {
        category: interval_metrics(
            calibrated_low[mask], median[mask], calibrated_high[mask], actual[mask]
        )["coverage"]
        for category in CATEGORIES
        if (mask := (x_test["category"] == category).to_numpy()).any()
    }

    metrics = {
        "mae_cop_per_kg": round(float(mae), 1),
        "baseline_mae_cop_per_kg": round(float(baseline_mae), 1),
        "improvement_over_baseline": round(float(1 - mae / baseline_mae), 3),
        "r2": round(float(r2_score(y_test, median)), 3),
        "mape": round(float(mean_absolute_percentage_error(y_test, median)), 3),
        "target_coverage": TARGET_COVERAGE,
        "interval_before_calibration": interval_metrics(low, median, high, actual),
        "interval_after_calibration": calibrated,
        "coverage_by_category": per_category,
    }

    trained_at = datetime.now(UTC)
    model_version = f"price-v2-{trained_at:%Y%m%d}"

    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    joblib.dump(
        {
            "format_version": MODEL_FORMAT_VERSION,
            "model_version": model_version,
            "feature_columns": FEATURE_COLUMNS,
            "sklearn_version": sklearn.__version__,
            "models": models,
            "conformal_margin": margin,
            "coverage": TARGET_COVERAGE,
        },
        MODEL_PATH,
        compress=3,
    )

    card = {
        "model": "Price suggestion for agricultural auctions",
        "version": model_version,
        "trained_at": trained_at.isoformat(timespec="seconds"),
        "algorithm": (
            "HistGradientBoostingRegressor, quantile loss (p10, p50, p90), "
            "range calibrated by conformalized quantile regression"
        ),
        "intended_use": "Suggest a starting price to producers. A human always decides.",
        "training_data": {
            "source": "Synthetic, generated by ml/dataset.py from reference prices",
            "rows": DATASET_ROWS,
            "seed": SEED,
            "splits": {"fit": 0.6, "calibration": 0.2, "test": 0.2},
        },
        "conformal_margin": round(margin, 4),
        "features": FEATURE_COLUMNS,
        "monotonic_constraints": MONOTONIC_CONSTRAINTS,
        "metrics": metrics,
        "limitations": [
            "Synthetic data: real prices may differ until retrained with SIPSA-DANE data.",
            "Reference prices are per category, not per specific product.",
            "Does not model transport costs or product quality grades.",
            "The coverage guarantee assumes new auctions resemble the calibration data.",
        ],
        "sklearn_version": sklearn.__version__,
    }
    MODEL_CARD_PATH.write_text(json.dumps(card, indent=2) + "\n", encoding="utf-8")

    print(f"Model {model_version} saved to {MODEL_PATH}")
    for name, value in metrics.items():
        print(f"  {name:28s} {value}")
    print(f"  conformal margin            {margin:.4f} of the median")


if __name__ == "__main__":
    main()
