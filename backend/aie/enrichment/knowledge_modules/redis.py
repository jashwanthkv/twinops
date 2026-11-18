"""Redis technical knowledge module.

Provides known failure modes, diagnostic patterns, monitoring requirements,
and adaptation opportunities for Redis cache and data store instances.
"""

from __future__ import annotations

from typing import Any


class RedisKnowledge:
    """Pre-curated operational and diagnostic knowledge for Redis."""

    PATTERNS: list[str] = [
        "cache_aside",
        "write_through",
        "distributed_lock",
        "session_store",
        "pub_sub_broker",
        "rate_limiter",
    ]

    FAILURE_SCENARIOS: list[dict[str, Any]] = [
        {
            "name": "memory_exhaustion_eviction",
            "scenario": "Redis maxmemory limit reached; key eviction triggered or OOM errors returned.",
            "impact": "High. Cache misses surge, slowing upstream services, or write operations fail with OOM.",
            "mitigation": "Increase maxmemory, configure volatile-lru/allkeys-lru eviction policy, or add TTLs.",
            "probability": "medium",
        },
        {
            "name": "connection_pool_exhaustion",
            "scenario": "Client connection pool runs out of available sockets due to spikes or leaked connections.",
            "impact": "High. Application requests block or time out waiting for a connection.",
            "mitigation": "Increase max_connections, implement connection timeouts, and verify connection closure.",
            "probability": "medium",
        },
        {
            "name": "network_partition",
            "scenario": "Network split between application instances and Redis master or replica nodes.",
            "impact": "Critical. Inability to read or write cache/state data.",
            "mitigation": "Configure sentinel or cluster failover, with fallback to primary database on cache errors.",
            "probability": "low",
        },
        {
            "name": "slow_commands_blocking_event_loop",
            "scenario": "Single-threaded event loop blocked by O(N) operations like KEYS *, SMEMBERS on large sets, or large FLUSHDB.",
            "impact": "High. All concurrent Redis operations stall, increasing system-wide latency.",
            "mitigation": "Replace KEYS with SCAN, use UNLINK instead of DEL for large keys, check slowlog.",
            "probability": "medium",
        },
        {
            "name": "ttl_misconfigurations",
            "scenario": "Keys inserted without TTLs or with synchronized expiration causing cache stampedes.",
            "impact": "Medium. Gradual memory growth or simultaneous DB query spikes on expiration.",
            "mitigation": "Enforce mandatory TTL with jitter on all temporary keys.",
            "probability": "high",
        },
    ]

    MONITORING_REQUIREMENTS: list[dict[str, Any]] = [
        {
            "metric": "memory_usage_bytes",
            "type": "gauge",
            "description": "Total bytes allocated to Redis (used_memory). Alert at >85% of maxmemory.",
            "threshold": "used_memory / maxmemory > 0.85",
        },
        {
            "metric": "connected_clients",
            "type": "gauge",
            "description": "Number of client connections currently open.",
            "threshold": "connected_clients > 500",
        },
        {
            "metric": "evicted_keys",
            "type": "counter",
            "description": "Rate of keys evicted due to maxmemory limit.",
            "threshold": "rate(evicted_keys) > 10/s",
        },
        {
            "metric": "keyspace_hits",
            "type": "counter",
            "description": "Number of successful key lookups.",
            "threshold": "none",
        },
        {
            "metric": "keyspace_misses",
            "type": "counter",
            "description": "Number of unsuccessful key lookups.",
            "threshold": "none",
        },
        {
            "metric": "hit_rate",
            "type": "gauge",
            "description": "Ratio of keyspace_hits to total lookups (hits + misses).",
            "threshold": "hit_rate < 0.75",
        },
        {
            "metric": "slow_log_len",
            "type": "gauge",
            "description": "Length and growth rate of Redis slow query log.",
            "threshold": "slow_log_len > 0",
        },
    ]

    ADAPTATION_OPPORTUNITIES: list[dict[str, Any]] = [
        {
            "opportunity": "Dynamic TTL scaling",
            "action": "Increase TTL for frequently accessed read-heavy keys during traffic surges.",
            "reversible": True,
            "confidence": 0.85,
            "risk": "Low. May serve slightly stale data depending on invalidation logic.",
        },
        {
            "opportunity": "Circuit breaker fallback on Redis timeout",
            "action": "Bypass cache and read directly from replica database when Redis p99 latency > 50ms.",
            "reversible": True,
            "confidence": 0.90,
            "risk": "Medium. Increased load on database replica.",
        },
        {
            "opportunity": "Connection pool autoscaling",
            "action": "Scale client pool size dynamically based on pool utilization.",
            "reversible": True,
            "confidence": 0.80,
            "risk": "Low.",
        },
    ]

    @classmethod
    def get_enrichment(cls) -> dict[str, Any]:
        """Return full enrichment knowledge for Redis."""
        return {
            "technology": "redis",
            "patterns": list(cls.PATTERNS),
            "failure_scenarios": list(cls.FAILURE_SCENARIOS),
            "monitoring_requirements": list(cls.MONITORING_REQUIREMENTS),
            "adaptation_opportunities": list(cls.ADAPTATION_OPPORTUNITIES),
        }
