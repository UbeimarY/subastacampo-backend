"""Loads the trained price model and serves predictions.

Only load artifacts produced by ml/train.py from this repository: joblib uses
pickle, which can run arbitrary code when loading untrusted files.
"""

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import joblib
import sklearn

from subastacampo.pricing.features import FEATURE_COLUMNS, PriceFeatures, to_frame

logger = logging.getLogger(__name__)

ARTIFACTS_DIR = Path(__file__).parent / "artifacts"
MODEL_PATH = ARTIFACTS_DIR / "price_model.joblib"
MODEL_CARD_PATH = ARTIFACTS_DIR / "model_card.json"
MODEL_FORMAT_VERSION = 1
QUANTILE_MODELS = ("low", "median", "high")


class ModelUnavailableError(Exception):
    """The artifact is missing or incompatible: the server must not start."""


@dataclass(frozen=True, slots=True)
class PricePerKg:
    low: float
    median: float
    high: float


class PricingModel:
    def __init__(self, artifact: dict[str, Any]) -> None:
        if artifact.get("format_version") != MODEL_FORMAT_VERSION:
            raise ModelUnavailableError("Unsupported model artifact format")
        if artifact.get("feature_columns") != FEATURE_COLUMNS:
            raise ModelUnavailableError("The model was trained with different features")
        if artifact.get("sklearn_version") != sklearn.__version__:
            logger.warning(
                "Model trained with scikit-learn %s, running %s",
                artifact.get("sklearn_version"),
                sklearn.__version__,
            )
        self.version: str = artifact["model_version"]
        self._models = artifact["models"]

    @classmethod
    def load(cls, path: Path = MODEL_PATH) -> "PricingModel":
        if not path.exists():
            raise ModelUnavailableError(
                f"Model artifact not found at {path}. Run: uv run python -m ml.train"
            )
        return cls(joblib.load(path))

    def predict(self, features: PriceFeatures) -> PricePerKg:
        """CPU-bound: call it from a worker thread inside async code."""
        frame = to_frame([features])
        # sorted() keeps low <= median <= high even if quantile models cross.
        low, median, high = sorted(
            float(self._models[name].predict(frame)[0]) for name in QUANTILE_MODELS
        )
        return PricePerKg(low=low, median=median, high=high)
