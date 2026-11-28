"""
client/simulate_traffic.py — CLI Traffic Generator for TwinOps Showcase Demo.

WHAT IT IS DOING:
  Sends real HTTP requests exclusively to the Client Application (http://localhost:8090).
  It does NOT send telemetry to TwinOps directly — telemetry is collected and emitted
  organically by `twinops_agent.py` inside the client application process!

PROFILES:
  --profile normal   : Nominal healthy traffic (Green HEALTHY in TwinOps)
  --profile average  : High-volume peak rush-hour traffic (Yellow/Elevated in TwinOps)
  --profile worst    : Severe cascade failure & bottleneck (Red CRITICAL in TwinOps)
  --profile auto     : Guided 2.5-minute evaluator presentation story
  --profile reset    : Immediately restores client app to normal healthy state
"""
from __future__ import annotations

import argparse
import concurrent.futures
import random
import sys
import time
import requests

CLIENT_URL = "http://localhost:8090"


def set_client_profile(profile: str) -> None:
    """Notify client application to switch internal component states."""
    try:
        url = f"{CLIENT_URL}/api/admin/profile"
        resp = requests.post(url, json={"mode": profile}, timeout=2.0)
        if resp.status_code == 200:
            print(f"[TrafficSim] Client app profile switched to: {profile.upper()}")
        else:
            print(f"[TrafficSim] Note: Could not set client profile (status {resp.status_code})")
    except Exception as exc:
        print(f"[TrafficSim] Warning: Client app at {CLIENT_URL} unreachable ({exc})")


def make_single_request() -> tuple[int, float]:
    """Execute a single HTTP request to the client application and return (status_code, latency_ms)."""
    start = time.time()
    try:
        # 75% read products (cache/db), 25% checkout orders (all 8 components)
        if random.random() < 0.75:
            resp = requests.get(f"{CLIENT_URL}/api/v1/store/products", timeout=5.0)
        else:
            payload = {
                "product_id": f"prod-{random.randint(1, 4)}",
                "quantity": random.randint(1, 3),
                "currency": "USD",
            }
            resp = requests.post(f"{CLIENT_URL}/api/v1/store/checkout", json=payload, timeout=5.0)
        elapsed = (time.time() - start) * 1000.0
        return resp.status_code, elapsed
    except requests.exceptions.Timeout:
        elapsed = (time.time() - start) * 1000.0
        return 504, elapsed
    except Exception:
        elapsed = (time.time() - start) * 1000.0
        return 500, elapsed


def run_traffic_loop(profile: str, duration_sec: int | None = None) -> None:
    """Run concurrent traffic generation loop for the chosen profile."""
    set_client_profile(profile)

    # Concurrency and delay settings per profile
    if profile == "normal":
        workers = 2
        delay = 0.25  # ~8 RPS
    elif profile == "average":
        workers = 6
        delay = 0.05  # ~35 RPS
    elif profile == "worst":
        workers = 12
        delay = 0.01  # ~80 RPS
    else:
        workers = 2
        delay = 0.2

    print(f"\n>>> Running [{profile.upper()}] Traffic Generator ({workers} workers) -> {CLIENT_URL}")
    print(">>> Press Ctrl+C at any time to stop.\n")

    start_time = time.time()
    total_reqs = 0
    ok_count = 0
    err_count = 0
    recent_latencies = []

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        while True:
            if duration_sec and (time.time() - start_time) >= duration_sec:
                break

            futures = [executor.submit(make_single_request) for _ in range(workers)]
            for f in concurrent.futures.as_completed(futures):
                code, lat = f.result()
                total_reqs += 1
                recent_latencies.append(lat)
                if len(recent_latencies) > 50:
                    recent_latencies.pop(0)

                if code < 400:
                    ok_count += 1
                else:
                    err_count += 1

            avg_lat = round(sum(recent_latencies) / len(recent_latencies), 1) if recent_latencies else 0.0
            elapsed_sec = int(time.time() - start_time)
            print(
                f"\r[{profile.upper()}] Elapsed: {elapsed_sec}s | Sent: {total_reqs} | "
                f"200 OK: {ok_count} | 4xx/5xx: {err_count} | Latency: {avg_lat}ms   ",
                end="",
                flush=True,
            )
            time.sleep(delay)

    print(f"\n>>> Completed {profile.upper()} profile ({total_reqs} requests sent).")


def run_auto_showcase() -> None:
    """Run the 4-act automated evaluator presentation story."""
    print("==============================================================")
    print("      TWINOPS EVALUATOR SHOWCASE — AUTO DEMO STORY")
    print("==============================================================")
    print("Act 1: NORMAL BASELINE (30s) -> System shows Green HEALTHY")
    print("Act 2: PEAK RUSH-HOUR  (30s) -> System shows Elevated Metrics")
    print("Act 3: CASCADE OUTAGE  (35s) -> System shows Red CRITICAL Anomaly")
    print("Act 4: SELF-HEALING    (25s) -> System automatically Recovers")
    print("==============================================================\n")

    # Act 1
    print("\n--- [ACT 1/4] NORMAL HEALTHY BASELINE ---")
    print("Evaluator cue: 'Notice baseline: <40ms latency, 0 errors, 90% cache hit.'")
    run_traffic_loop("normal", duration_sec=30)

    # Act 2
    print("\n--- [ACT 2/4] AVERAGE PEAK RUSH-HOUR LOAD ---")
    print("Evaluator cue: 'Higher traffic volume, metrics elevate without false alarm.'")
    run_traffic_loop("average", duration_sec=30)

    # Act 3
    print("\n--- [ACT 3/4] WORST-CASE OUTAGE & ANOMALY TRIGGER ---")
    print("Evaluator cue: 'Watch TwinOps: Isolation Forest triggers, health turns RED, RCA highlights DB leak.'")
    run_traffic_loop("worst", duration_sec=35)

    # Act 4
    print("\n--- [ACT 4/4] AUTONOMOUS RECOVERY & SELF-HEALING ---")
    print("Evaluator cue: 'Bottleneck cleared; TwinOps MAPE-K loop restores status to GREEN HEALTHY.'")
    set_client_profile("normal")
    run_traffic_loop("normal", duration_sec=25)

    print("\n==============================================================")
    print("   EVALUATOR SHOWCASE COMPLETE! System restored to healthy.")
    print("==============================================================\n")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="TwinOps Client Traffic Simulator for Evaluator Demo",
    )
    parser.add_argument(
        "--profile",
        choices=["normal", "average", "worst", "auto", "reset"],
        default="normal",
        help="Select traffic and metric profile (default: normal)",
    )
    parser.add_argument(
        "--duration",
        type=int,
        default=None,
        help="Optional duration in seconds to run (default: run indefinitely until Ctrl+C)",
    )

    args = parser.parse_args()

    if args.profile == "reset":
        set_client_profile("normal")
        print("[TrafficSim] Client app reset to normal healthy state.")
        return

    if args.profile == "auto":
        run_auto_showcase()
        return

    try:
        run_traffic_loop(args.profile, duration_sec=args.duration)
    except KeyboardInterrupt:
        print("\n\n[TrafficSim] Stopped by user. Resetting client app to normal...")
        set_client_profile("normal")
        print("[TrafficSim] Clean exit.")


if __name__ == "__main__":
    main()
