"""
ml/anomaly_detector.py

WHAT IT IS DOING:
  Wraps scikit-learn IsolationForest with per-app_id model instances.
  Accepts the 18-feature vector, returns a dict with timestamp, score, alert
  flag, and top_metric (the most anomalous feature by z-score).
  Uses a warm-restart strategy: retrain from scratch every RETRAIN_AFTER
  observations using the last TRAIN_BUFFER_SIZE samples stored in Redis.

HOW IT IS CODED:
  AnomalyDetector.predict(app_id, features, mean, std) → AnomalyResult.
  Models are stored in a dict keyed by app_id. Training samples are stored
  in Redis ZADD sorted by arrival timestamp and trimmed to TRAIN_BUFFER_SIZE.
  Retrain triggers when count % RETRAIN_AFTER == 0 and count >= MIN_TRAIN.

DIAGNOSTIC GUIDE:
  If alert=True immediately on startup, the model has not been trained on
  enough data yet (count < MIN_TRAIN) — alerts are suppressed until MIN_TRAIN
  observations are collected. ZCARD ml:train:{app_id} shows the buffer size.
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone

import numpy as np
import redis
from sklearn.ensemble import IsolationForest

from ml.feature_extractor import FEATURE_NAMES, top_deviated_metric

logger = logging.getLogger(__name__)

MIN_TRAIN = 50           # min observations before first training
RETRAIN_AFTER = 200      # retrain every N new observations
TRAIN_BUFFER_SIZE = 500  # keep last N samples for retraining
ANOMALY_THRESHOLD = float(os.getenv("ANOMALY_THRESHOLD", "0.0"))  # IF score threshold (negative = anomalous)
CONTAMINATION = float(os.getenv("IF_CONTAMINATION", "0.05"))


@dataclass
class AnomalyResult:
    timestamp: str
    score: float
    alert: bool
    top_metric: str


class AnomalyDetector:
    """Per-app_id Isolation Forest anomaly detector."""

    def __init__(self, r: redis.Redis) -> None:
        self._r = r
        self._models: dict[str, IsolationForest] = {}
        self._counts: dict[str, int] = {}

    def _train_key(self, app_id: str) -> str:
        return f"ml:train:{app_id}"

    def _add_sample(self, app_id: str, features: np.ndarray) -> int:
        key = self._train_key(app_id)
        ts = time.time()
        self._r.zadd(key, {json.dumps(features.tolist()): ts})
        self._r.zremrangebyrank(key, 0, -(TRAIN_BUFFER_SIZE + 1))
        count = self._r.zcard(key)
        return int(count)

    def _load_training_data(self, app_id: str) -> np.ndarray | None:
        key = self._train_key(app_id)
        raw = self._r.zrange(key, 0, -1)
        if not raw:
            return None
        rows = []
        for item in raw:
            try:
                rows.append(json.loads(item))
            except json.JSONDecodeError:
                continue
        if len(rows) < MIN_TRAIN:
            return None
        return np.array(rows, dtype=np.float64)

    def _retrain(self, app_id: str) -> None:
        X = self._load_training_data(app_id)
        if X is None:
            logger.debug("Not enough data to train anomaly model for %s", app_id)
            return
        model = IsolationForest(
            n_estimators=100,
            contamination=CONTAMINATION,
            random_state=42,
            n_jobs=-1,
        )
        model.fit(X)
        self._models[app_id] = model
        logger.info("Anomaly model retrained for %s with %d samples", app_id, len(X))

    def predict(
        self,
        app_id: str,
        features: np.ndarray,
        mean: np.ndarray,
        std: np.ndarray,
    ) -> AnomalyResult:
        count = self._add_sample(app_id, features)
        self._counts[app_id] = count

        # Trigger retrain on first MIN_TRAIN, then every RETRAIN_AFTER
        if count == MIN_TRAIN or (count > MIN_TRAIN and count % RETRAIN_AFTER == 0):
            self._retrain(app_id)

        ts = datetime.now(tz=timezone.utc).isoformat()
        model = self._models.get(app_id)

        if model is None:
            # Not enough data yet — no alert
            return AnomalyResult(timestamp=ts, score=0.0, alert=False, top_metric="")

        X = features.reshape(1, -1)
        raw_score = float(model.score_samples(X)[0])
        # score_samples returns negative values; more negative = more anomalous
        # We negate so higher score = more anomalous for readability
        score = round(-raw_score, 4)
        alert = raw_score < ANOMALY_THRESHOLD
        top = top_deviated_metric(features, mean, std) if alert else ""
        return AnomalyResult(timestamp=ts, score=score, alert=alert, top_metric=top)
