import os
import sys
from pathlib import Path

# Add backend to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

import fakeredis
import numpy as np

from ml.anomaly_detector import AnomalyDetector
from ml.forecaster import Forecaster
from ml.rolling_stats import update_stats
from ml.rolling_window import get_window_values, push_metric

def test_fifteen_point_window():
    print("Testing 15-point windowing policy...")
    r = fakeredis.FakeRedis(decode_responses=True)
    app_id = "test-app"
    metric = "cpu_usage_pct"

    detector = AnomalyDetector(r)
    forecaster = Forecaster()

    # 1. Simulate first 14 data points
    print("\n--- Phase 1: Ingesting points 1 to 14 (Buffering) ---")
    for i in range(1, 15):
        features = np.full(18, float(i))
        mean, std, count = update_stats(r, app_id, features)
        push_metric(r, app_id, metric, float(i))
        res = detector.predict(app_id, features, mean, std)
        win = get_window_values(r, app_id, metric)

        assert count == i, f"Expected count {i}, got {count}"
        assert not res.alert, f"Alert should be False before 15 points (got True at point {i})"
        assert res.score == 0.0, f"Score should be 0.0 before 15 points (got {res.score})"
        assert win is None, f"Window values should be None before 15 points (got length {len(win) if win is not None else 0})"

    print("PASS: Points 1 to 14 buffered, ML inference suppressed, window is None.")

    # 2. Ingest 15th data point
    print("\n--- Phase 2: Ingesting 15th data point (First trigger) ---")
    features_15 = np.full(18, 15.0)
    mean, std, count = update_stats(r, app_id, features_15)
    push_metric(r, app_id, metric, 15.0)
    res_15 = detector.predict(app_id, features_15, mean, std)
    win_15 = get_window_values(r, app_id, metric)

    assert count == 15, f"Expected count 15, got {count}"
    assert win_15 is not None, "Window should NOT be None at 15 points"
    assert len(win_15) == 15, f"Expected window length 15, got {len(win_15)}"
    assert np.allclose(win_15, np.arange(1, 16)), f"Expected window 1..15, got {win_15}"

    # Verify forecaster works with 15 values
    forecaster.update(app_id, metric, win_15, 16.0)
    forecaster.update(app_id, metric, win_15, 16.5)
    pred = forecaster.predict(app_id, metric, win_15)
    print(f"Forecaster prediction on 15 points: {pred}")
    assert pred is not None, "Forecaster should predict on 15 points"

    print("PASS: 15th data point successfully activated ML and windowing with strictly 15 points.")

    # 3. Ingest points 16 to 25 (Exceeding 15 points: check sliding window of last 15)
    print("\n--- Phase 3: Ingesting points 16 to 25 (Sliding window: last 15 data points) ---")
    for i in range(16, 26):
        features_i = np.full(18, float(i))
        mean, std, count = update_stats(r, app_id, features_i)
        push_metric(r, app_id, metric, float(i))
        res_i = detector.predict(app_id, features_i, mean, std)
        win_i = get_window_values(r, app_id, metric)

        assert count == i
        assert win_i is not None
        assert len(win_i) == 15, f"Window must strictly have 15 points, got {len(win_i)}"
        # Check that it contains strictly the LAST 15 points: [i - 14, ..., i]
        expected_last_15 = np.arange(i - 14, i + 1, dtype=np.float64)
        assert np.allclose(win_i, expected_last_15), f"Expected {expected_last_15}, got {win_i}"

    print(f"Final count in stats: {count}")
    print(f"Final window (last 15 of 25 points): {win_i.tolist()}")
    print("PASS: Window strictly keeps and uses the last 15 data points!")

if __name__ == "__main__":
    test_fifteen_point_window()
