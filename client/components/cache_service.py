"""
client/components/cache_service.py — Redis Cache Layer with Hit/Miss Tracking.
Supports cache miss storm / cache bust fault.
"""
from __future__ import annotations
import json
import logging
import threading
import redis
from client.config import settings
from client.chaos_state import chaos

logger = logging.getLogger("client.cache")

class CacheService:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._hits: int = 0
        self._misses: int = 0
        self._local_cache: dict[str, str] = {}
        self._redis_client: redis.Redis | None = None
        try:
            r = redis.from_url(settings.redis_url, decode_responses=True, socket_timeout=1.0)
            r.ping()
            self._redis_client = r
            logger.info("Connected to Redis cache on %s", settings.redis_url)
        except Exception as exc:
            logger.warning("Redis unavailable (%s), using in-memory LRU cache fallback", exc)
            self._redis_client = None

    def get(self, key: str) -> dict | list | None:
        # If cache bust is enabled, simulate cache miss storm
        if chaos.cache_bust_enabled:
            with self._lock:
                self._misses += 1
            return None

        val = None
        if self._redis_client:
            try:
                val = self._redis_client.get(key)
            except Exception:
                val = self._local_cache.get(key)
        else:
            val = self._local_cache.get(key)

        with self._lock:
            if val is not None:
                self._hits += 1
                try:
                    return json.loads(val)
                except Exception:
                    return val
            else:
                self._misses += 1
                return None

    def set(self, key: str, value: dict | list, ttl_seconds: int = 60) -> None:
        serialized = json.dumps(value)
        if self._redis_client:
            try:
                self._redis_client.setex(key, ttl_seconds, serialized)
                return
            except Exception:
                pass
        with self._lock:
            self._local_cache[key] = serialized

    def get_hit_ratio(self) -> float:
        with self._lock:
            total = self._hits + self._misses
            if total == 0:
                return 0.90  # nominal default baseline
            ratio = round(self._hits / total, 3)
            # Reset counters periodically to reflect sliding window
            if total > 500:
                self._hits = int(self._hits * 0.2)
                self._misses = int(self._misses * 0.2)
            return ratio

cache_service = CacheService()
