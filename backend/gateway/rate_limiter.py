"""
gateway/rate_limiter.py

Sliding-window rate limiter using Redis for the TwinOps Gateway.

WHAT IT IS DOING:
  Prevents any single application from flooding the Gateway and starving
  other tenants. Limits are enforced per application_id using a Redis
  sorted-set sliding window (ZADD + ZREMRANGEBYSCORE + ZCARD in a pipeline).

HOW IT IS CODED:
  RateLimiter.check() executes a three-command Redis pipeline:
    1. ZADD <key> NX <now_ms> <now_ms>   — record this request's timestamp
    2. ZREMRANGEBYSCORE <key> 0 <cutoff> — remove requests outside window
    3. ZCARD <key>                        — count requests in window
    4. EXPIRE <key> <window_seconds>      — auto-cleanup idle keys
  If ZCARD > limit, raises HTTP 429.

  Default limits (env-overridable):
    GATEWAY_RATE_LIMIT_REQUESTS = 300   (requests per window)
    GATEWAY_RATE_LIMIT_WINDOW_S = 60    (window size in seconds)

DIAGNOSTIC GUIDE:
  429 Too Many Requests → application is sending faster than the limit.
    - Check GATEWAY_RATE_LIMIT_* env vars for current thresholds.
    - Verify SDK flush interval; it should not be < 1 second.
    - If legitimate traffic, raise GATEWAY_RATE_LIMIT_REQUESTS.
  Redis latency > 5ms per check → pipeline is not being used; check code.
  Keys piling up → EXPIRE is not being set; ensure Redis eviction policy.
"""

from __future__ import annotations

import logging
import os
import time

import redis
from fastapi import HTTPException, status

logger = logging.getLogger(__name__)

RATE_LIMIT_REQUESTS = int(os.getenv("GATEWAY_RATE_LIMIT_REQUESTS", "300"))
RATE_LIMIT_WINDOW_S = int(os.getenv("GATEWAY_RATE_LIMIT_WINDOW_S", "60"))

_RATE_KEY_PREFIX = "gateway:ratelimit:"


class RateLimiter:
    """Sliding-window rate limiter backed by Redis sorted sets.

    HOW IT IS CODED:
      Uses a Redis sorted set where member = score = current timestamp in ms.
      A pipeline removes stale members, counts remaining, and sets TTL — all
      atomically from the client's perspective (single round-trip).

    WHAT IT IS DOING:
      Guards the telemetry endpoint. Each call to check() increments the
      counter for the given app_id. If the counter exceeds the limit within
      the window, a 429 is raised before the request reaches validation or
      the Bus.

    DIAGNOSTIC GUIDE:
      If rate limiting is not activating when expected, confirm that
      Redis decode_responses=False for the rate limiter client (sorted set
      scores are numbers, not strings). This class uses its own connection
      with decode_responses=False.
    """

    def __init__(self, redis_client: redis.Redis) -> None:
        self._r = redis_client

    def check(
        self,
        app_id: str,
        limit: int = RATE_LIMIT_REQUESTS,
        window_s: int = RATE_LIMIT_WINDOW_S,
    ) -> None:
        """Check and increment the rate counter for app_id.

        Raises HTTP 429 if the application has exceeded `limit` requests
        in the last `window_s` seconds.
        """
        key = _RATE_KEY_PREFIX + app_id
        now_ms = int(time.time() * 1000)
        cutoff_ms = now_ms - window_s * 1000

        pipe = self._r.pipeline(transaction=False)
        pipe.zadd(key, {str(now_ms): now_ms})
        pipe.zremrangebyscore(key, 0, cutoff_ms)
        pipe.zcard(key)
        pipe.expire(key, window_s * 2)
        results = pipe.execute()

        count = results[2]  # ZCARD result
        if count > limit:
            retry_after = window_s
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail={
                    "code": "RATE_LIMIT_EXCEEDED",
                    "message": f"Rate limit of {limit} requests per {window_s}s exceeded",
                    "retry_after_seconds": retry_after,
                },
                headers={"Retry-After": str(retry_after)},
            )
        logger.debug("Rate check app_id=%s count=%d/%d", app_id, count, limit)


_rate_limiter: RateLimiter | None = None


def get_rate_limiter() -> RateLimiter:
    global _rate_limiter
    if _rate_limiter is None:
        url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
        # decode_responses=False so sorted set scores stay numeric
        r = redis.Redis.from_url(url, decode_responses=False, socket_connect_timeout=5)
        _rate_limiter = RateLimiter(r)
    return _rate_limiter
