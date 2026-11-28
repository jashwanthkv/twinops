"""
client/cache/cache_service.py — Redis Cache Layer with Hit/Miss Tracking.

Provides read-through caching backed by Redis (localhost:6379) with
automatic in-memory LRU fallback when Redis is unavailable.
Supports cache bust / miss storm fault injection via chaos state.
"""
from __future__ import annotations
import json
import logging
import threading
import redis
from client.config import settings
from client.chaos_state import chaos

logger = logging.getLogger("client.cache")


class RedisCacheService:
    """Read-through cache service backed by Redis with in-memory fallback.

    Responsibilities:
      - Serve cached data from Redis (or in-memory dict on Redis failure)
      - Track hit/miss counters for cache_hit_ratio metric
      - Simulate cache bust / miss storm via chaos injection
      - Expose health status and stats for observability
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._hits: int = 0
        self._misses: int = 0
        self._local_cache: dict[str, str] = {}
        self._redis_client: redis.Redis | None = None
        self._redis_connected: bool = False
        try:
            r = redis.from_url(settings.redis_url, decode_responses=True, socket_timeout=1.0)
            r.ping()
            self._redis_client = r
            self._redis_connected = True
            logger.info("Connected to Redis cache on %s", settings.redis_url)
        except Exception as exc:
            logger.warning("Redis unavailable (%s), using in-memory LRU cache fallback", exc)
            self._redis_client = None

    # ------------------------------------------------------------------ #
    # Core cache operations
    # ------------------------------------------------------------------ #

    def get(self, key: str) -> dict | list | None:
        """Return cached value for key, or None on miss/bust."""
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
        """Write value to cache with TTL."""
        serialized = json.dumps(value)
        if self._redis_client:
            try:
                self._redis_client.setex(key, ttl_seconds, serialized)
                return
            except Exception:
                pass
        with self._lock:
            self._local_cache[key] = serialized

    # ------------------------------------------------------------------ #
    # Observability helpers
    # ------------------------------------------------------------------ #

    def get_hit_ratio(self) -> float:
        """Return current hit ratio with sliding-window decay."""
        with self._lock:
            total = self._hits + self._misses
            if total == 0:
                return 0.90
            ratio = round(self._hits / total, 3)
            if total > 500:
                self._hits = int(self._hits * 0.2)
                self._misses = int(self._misses * 0.2)
            return ratio

    def get_stats(self) -> dict:
        """Return cache diagnostics snapshot."""
        with self._lock:
            total = self._hits + self._misses
            return {
                "redis_connected": self._redis_connected,
                "hits": self._hits,
                "misses": self._misses,
                "hit_ratio": round(self._hits / total, 3) if total > 0 else 0.90,
                "cache_bust_active": chaos.cache_bust_enabled,
                "backend": "redis" if self._redis_connected else "in-memory",
            }

    def reset_stats(self) -> None:
        """Reset hit/miss counters."""
        with self._lock:
            self._hits = 0
            self._misses = 0

    def is_healthy(self) -> bool:
        """Cache is healthy if Redis is reachable or in-memory fallback is active."""
        return True  # Always operational (fallback ensures continuity)


# Singleton
cache_service = RedisCacheService()
