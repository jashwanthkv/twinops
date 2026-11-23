"""
ml/forecaster.py

WHAT IT IS DOING:
  Maintains per-(app_id, metric_name) online linear regression models using
  SGDRegressor with partial_fit(). After every observation it updates the model
  weights incrementally. Saves weights to disk every SAVE_EVERY updates.
  On startup it reloads saved weights so the model never cold-starts.

HOW IT IS CODED:
  Forecaster.update(app_id, metric, X_window, y_target) calls partial_fit.
  X_window is the 10-reading numpy array + 3 engineered features (slope_last_3,
  mean_10, std_10) = 13 features total.
  Forecaster.predict(app_id, metric, X_window) returns the predicted value.
  Models are keyed by "{app_id}:{metric}" in self._models dict.
  Weights dir: ml_weights/ under the backend directory.

DIAGNOSTIC GUIDE:
  If predictions are way off, check the R2 score in MongoDB ml_models collection.
  If R2 is negative, the model needs more data. Drift triggers a hard retrain
  from the last 500 stored window observations.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

import joblib
import numpy as np
from sklearn.linear_model import SGDRegressor

logger = logging.getLogger(__name__)

SAVE_EVERY = 50        # save weights every N updates
WEIGHTS_DIR = Path(__file__).resolve().parent.parent / "ml_weights"
MAE_WINDOW = 50        # track MAE over last N predictions
DRIFT_MAE_RATIO = 3.0  # if MAE > 3x initial MAE → hard drift


def _build_X(window: np.ndarray) -> np.ndarray:
    """Build 18-feature vector from strictly the last 15-value window + engineered features."""
    w = window[-15:] if len(window) >= 15 else window
    slope_last3 = float(np.polyfit(range(3), w[-3:], 1)[0]) if len(w) >= 3 else 0.0
    mean15 = float(np.mean(w))
    std15 = float(np.std(w))
    return np.concatenate([w, [slope_last3, mean15, std15]])


class Forecaster:
    """Per-(app_id, metric) online linear regression with weight persistence."""

    def __init__(self) -> None:
        self._models: dict[str, SGDRegressor] = {}
        self._update_counts: dict[str, int] = {}
        self._mae_errors: dict[str, list[float]] = {}
        WEIGHTS_DIR.mkdir(parents=True, exist_ok=True)

    def _key(self, app_id: str, metric: str) -> str:
        return f"{app_id}:{metric}"

    def _weight_path(self, app_id: str, metric: str) -> Path:
        safe_app = app_id.replace("/", "_").replace(":", "_")
        return WEIGHTS_DIR / safe_app / f"{metric}.pkl"

    def _get_model(self, app_id: str, metric: str) -> SGDRegressor:
        key = self._key(app_id, metric)
        if key not in self._models:
            path = self._weight_path(app_id, metric)
            if path.exists():
                try:
                    self._models[key] = joblib.load(path)
                    logger.info("Loaded forecaster weights for %s/%s", app_id, metric)
                except Exception as exc:
                    logger.warning("Failed to load weights for %s/%s: %s", app_id, metric, exc)
            if key not in self._models:
                self._models[key] = SGDRegressor(
                    loss="squared_error",
                    learning_rate="adaptive",
                    eta0=0.01,
                    warm_start=True,
                    max_iter=1,
                    tol=None,
                )
        return self._models[key]

    def update(self, app_id: str, metric: str, window: np.ndarray, y_target: float) -> None:
        """Update model weights with one new (X_window → y_target) observation using last 15 values."""
        if len(window) < 15:
            return
        w = window[-15:]
        model = self._get_model(app_id, metric)
        X = _build_X(w).reshape(1, -1)

        # Handle dimension migration (e.g. from 13 to 18 features)
        if hasattr(model, "coef_") and model.coef_.shape[0] != X.shape[1]:
            logger.info("Resetting SGDRegressor for %s/%s due to feature size change (%d -> %d)",
                        app_id, metric, model.coef_.shape[0], X.shape[1])
            model = SGDRegressor(
                loss="squared_error",
                learning_rate="adaptive",
                eta0=0.01,
                warm_start=True,
                max_iter=1,
                tol=None,
            )
            self._models[self._key(app_id, metric)] = model

        y = np.array([y_target])

        # Track prediction error before update (if model is initialized)
        key = self._key(app_id, metric)
        count = self._update_counts.get(key, 0)
        if count >= 1 and hasattr(model, "coef_") and model.coef_.shape[0] == X.shape[1]:
            try:
                pred = model.predict(X)[0]
                err = abs(pred - y_target)
                errs = self._mae_errors.setdefault(key, [])
                errs.append(err)
                if len(errs) > MAE_WINDOW:
                    errs.pop(0)
            except Exception:
                pass

        model.partial_fit(X, y)
        count += 1
        self._update_counts[key] = count

        if count % SAVE_EVERY == 0:
            self._save(app_id, metric, model, count)

    def predict(self, app_id: str, metric: str, window: np.ndarray) -> float | None:
        """Return predicted value at t+15min using strictly the last 15 values, or None if not ready."""
        if len(window) < 15:
            return None
        w = window[-15:]
        key = self._key(app_id, metric)
        model = self._models.get(key)
        if model is None or self._update_counts.get(key, 0) < 2:
            return None
        try:
            X = _build_X(w).reshape(1, -1)
            if hasattr(model, "coef_") and model.coef_.shape[0] != X.shape[1]:
                return None
            return float(model.predict(X)[0])
        except Exception as exc:
            logger.warning("Prediction failed for %s/%s: %s", app_id, metric, exc)
            return None

    def _save(self, app_id: str, metric: str, model: SGDRegressor, count: int) -> None:
        path = self._weight_path(app_id, metric)
        path.parent.mkdir(parents=True, exist_ok=True)
        joblib.dump(model, path)
        logger.debug("Saved forecaster weights for %s/%s (update #%d)", app_id, metric, count)
