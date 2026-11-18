"""
aie/persistence/redis/client.py — Asynchronous Redis Client & In-Memory Fallback.

Provides fast in-memory access using redis.asyncio with graceful in-memory
fallback if the Redis daemon is offline.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from aie.config import settings

logger = logging.getLogger(__name__)


class InMemoryRedisMock:
    """High-speed in-memory dict-based fallback when Redis daemon is not running."""

    def __init__(self) -> None:
        self._data: dict[str, str] = {}
        self._sets: dict[str, set[str]] = {}

    async def ping(self) -> bool:
        return True

    async def get(self, key: str) -> str | None:
        return self._data.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> bool:
        self._data[key] = str(value)
        return True

    async def delete(self, *keys: str) -> int:
        count = 0
        for k in keys:
            if k in self._data:
                del self._data[k]
                count += 1
            if k in self._sets:
                del self._sets[k]
                count += 1
        return count

    async def keys(self, pattern: str = "*") -> list[str]:
        import fnmatch

        all_keys = list(self._data.keys()) + list(self._sets.keys())
        return [k for k in set(all_keys) if fnmatch.fnmatch(k, pattern)]

    async def sadd(self, key: str, *members: str) -> int:
        if key not in self._sets:
            self._sets[key] = set()
        added = 0
        for m in members:
            if m not in self._sets[key]:
                self._sets[key].add(str(m))
                added += 1
        return added

    async def smembers(self, key: str) -> set[str]:
        return set(self._sets.get(key, set()))

    async def srem(self, key: str, *members: str) -> int:
        if key not in self._sets:
            return 0
        removed = 0
        for m in members:
            if m in self._sets[key]:
                self._sets[key].remove(m)
                removed += 1
        return removed

    async def scard(self, key: str) -> int:
        return len(self._sets.get(key, set()))

    async def close(self) -> None:
        pass


class RedisClient:
    """Manages Redis connection lifecycle with seamless fallback."""

    def __init__(self, url: str | None = None) -> None:
        self.url = url or settings.redis_url
        self._client: Any = None
        self._is_live_redis: bool = False

    async def connect(self) -> bool:
        """Connect to live Redis or initialize in-memory fallback."""
        if self._client is not None:
            return True

        import os
        import socket
        from urllib.parse import urlparse

        if os.environ.get("AIE_DISABLE_LIVE_REDIS", "").lower() in ("1", "true"):
            self._client = InMemoryRedisMock()
            self._is_live_redis = False
            return True

        # Fast pre-flight socket probe (100ms) to avoid Windows TCP handshake hangs
        try:
            parsed = urlparse(self.url)
            host = parsed.hostname or "127.0.0.1"
            port = parsed.port or 6379

            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(0.1)
            err = sock.connect_ex((host, port))
            sock.close()
            if err != 0:
                logger.info("Redis host %s:%d is unreachable (code %d). Using in-memory fallback.", host, port, err)
                self._client = InMemoryRedisMock()
                self._is_live_redis = False
                return True
        except Exception as probe_err:
            logger.debug("Socket probe error: %s", probe_err)
            self._client = InMemoryRedisMock()
            self._is_live_redis = False
            return True

        try:
            import redis.asyncio as aioredis

            client = aioredis.from_url(
                self.url,
                decode_responses=True,
                socket_timeout=1.0,
                socket_connect_timeout=1.0,
            )
            await client.ping()
            self._client = client
            self._is_live_redis = True
            logger.info("Successfully connected to live Redis at %s", self.url)
            return True
        except Exception as exc:
            logger.info(
                "Live Redis unavailable at %s (%s). Using high-speed in-memory store fallback.",
                self.url,
                exc,
            )
            self._client = InMemoryRedisMock()
            self._is_live_redis = False
            return True

    async def disconnect(self) -> None:
        if self._client is not None:
            await self._client.close()
            self._client = None
            self._is_live_redis = False

    @property
    def client(self) -> Any:
        if self._client is None:
            self._client = InMemoryRedisMock()
        return self._client

    @property
    def is_live_redis(self) -> bool:
        return self._is_live_redis


# Global Redis singleton
redis_client = RedisClient()
