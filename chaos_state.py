"""
client/chaos_state.py — Central state and chaos injection toggles for the client app.
Controls operational profiles: normal, average, worst.
"""
from __future__ import annotations
import threading
from typing import Literal

ProfileMode = Literal["normal", "average", "worst"]

class ChaosState:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.profile: ProfileMode = "normal"
        # Fault toggles
        self.db_leak_enabled: bool = False
        self.db_extra_delay_ms: int = 0
        self.cache_bust_enabled: bool = False
        self.payment_delay_ms: int = 40
        self.payment_fail_ratio: float = 0.0
        self.rate_limit_choke: bool = False
        self.worker_stalled: bool = False

    def set_profile(self, mode: ProfileMode) -> None:
        with self._lock:
            self.profile = mode
            if mode == "normal":
                self.db_leak_enabled = False
                self.db_extra_delay_ms = 0
                self.cache_bust_enabled = False
                self.payment_delay_ms = 35
                self.payment_fail_ratio = 0.0
                self.rate_limit_choke = False
                self.worker_stalled = False
            elif mode == "average":
                self.db_leak_enabled = False
                self.db_extra_delay_ms = 50
                self.cache_bust_enabled = False
                self.payment_delay_ms = 120
                self.payment_fail_ratio = 0.02
                self.rate_limit_choke = False
                self.worker_stalled = False
            elif mode == "worst":
                self.db_leak_enabled = True
                self.db_extra_delay_ms = 1200
                self.cache_bust_enabled = True
                self.payment_delay_ms = 2800
                self.payment_fail_ratio = 0.35
                self.rate_limit_choke = True
                self.worker_stalled = True

    def reset(self) -> None:
        self.set_profile("normal")

    def to_dict(self) -> dict:
        with self._lock:
            return {
                "profile": self.profile,
                "db_leak_enabled": self.db_leak_enabled,
                "db_extra_delay_ms": self.db_extra_delay_ms,
                "cache_bust_enabled": self.cache_bust_enabled,
                "payment_delay_ms": self.payment_delay_ms,
                "payment_fail_ratio": self.payment_fail_ratio,
                "rate_limit_choke": self.rate_limit_choke,
                "worker_stalled": self.worker_stalled,
            }

chaos = ChaosState()
