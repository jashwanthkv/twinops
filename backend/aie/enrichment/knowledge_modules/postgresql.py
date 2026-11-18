"""PostgreSQL technical knowledge module.

Provides known failure modes, diagnostic patterns, monitoring requirements,
and adaptation opportunities for PostgreSQL database components.
"""

from __future__ import annotations

from typing import Any


class PostgreSQLKnowledge:
    """Pre-curated operational and diagnostic knowledge for PostgreSQL."""

    PATTERNS: list[str] = [
        "relational_data_store",
        "read_write_splitting",
        "connection_pooling",
        "transaction_management",
        "jsonb_document_store",
    ]

    FAILURE_SCENARIOS: list[dict[str, Any]] = [
        {
            "name": "connection_pool_exhaustion",
            "scenario": "Total active and idle backend connections exceed max_connections or pool capacity.",
            "impact": "Critical. Incoming queries are rejected with 'FATAL: remaining connection slots are reserved'.",
            "mitigation": "Deploy PgBouncer for transaction-level pooling and tune client pool sizing.",
            "probability": "medium",
        },
        {
            "name": "deadlocks",
            "scenario": "Two concurrent transactions acquire conflicting locks on rows in opposing order.",
            "impact": "High. One transaction is abruptly aborted with a deadlock detected error.",
            "mitigation": "Enforce consistent row-locking order across application queries and reduce transaction lengths.",
            "probability": "medium",
        },
        {
            "name": "long_running_queries",
            "scenario": "Unindexed sequential scans or poorly planned queries hold locks and consume CPU/IO.",
            "impact": "High. Degrades overall database throughput and prevents table vacuuming.",
            "mitigation": "Set statement_timeout, analyze slow queries with pg_stat_statements, and add composite indexes.",
            "probability": "high",
        },
        {
            "name": "autovacuum_blocking",
            "scenario": "Long-running transactions hold open xmin horizons, preventing autovacuum from cleaning dead tuples.",
            "impact": "High. Table and index bloat, causing severe disk consumption and cache degradation.",
            "mitigation": "Tune autovacuum_vacuum_cost_limit, autovacuum_max_workers, and eliminate idle-in-transaction connections.",
            "probability": "medium",
        },
        {
            "name": "replication_lag",
            "scenario": "Streaming replication replicas fall behind the primary due to network saturation or heavy write bursts.",
            "impact": "Medium to High. Stale reads from read-replicas, risk of failover data loss.",
            "mitigation": "Monitor pg_stat_replication replay_lag and scale replica disk IOPS.",
            "probability": "medium",
        },
    ]

    MONITORING_REQUIREMENTS: list[dict[str, Any]] = [
        {
            "metric": "active_connections",
            "type": "gauge",
            "description": "Number of sessions currently executing a query in pg_stat_activity.",
            "threshold": "active_connections > 0.8 * max_connections",
        },
        {
            "metric": "idle_connections",
            "type": "gauge",
            "description": "Connections idling or idle-in-transaction.",
            "threshold": "idle_in_transaction > 10 for > 30s",
        },
        {
            "metric": "pool_pending",
            "type": "gauge",
            "description": "Client threads waiting for a free database connection from the pool.",
            "threshold": "pool_pending > 0",
        },
        {
            "metric": "query_latency_p95",
            "type": "gauge",
            "description": "95th percentile query execution duration.",
            "threshold": "query_latency_p95 > 250ms",
        },
        {
            "metric": "deadlocks",
            "type": "counter",
            "description": "Count of deadlock detection events from pg_stat_database.",
            "threshold": "rate(deadlocks) > 0",
        },
        {
            "metric": "table_bloat",
            "type": "gauge",
            "description": "Estimated dead tuple percentage per table from pg_stat_user_tables.",
            "threshold": "dead_tuple_ratio > 0.20",
        },
    ]

    ADAPTATION_OPPORTUNITIES: list[dict[str, Any]] = [
        {
            "opportunity": "Dynamic statement timeout adjustment",
            "action": "Lower statement_timeout for interactive web queries during high-load periods.",
            "reversible": True,
            "confidence": 0.85,
            "risk": "Low. Aborts long-running queries to protect general availability.",
        },
        {
            "opportunity": "Read traffic shedding to replicas",
            "action": "Shift SELECT-only query traffic from primary to standby replica nodes.",
            "reversible": True,
            "confidence": 0.90,
            "risk": "Low. May introduce slight eventual consistency lag.",
        },
        {
            "opportunity": "Work_mem dynamic scaling",
            "action": "Temporarily allocate higher work_mem for critical batch reporting jobs.",
            "reversible": True,
            "confidence": 0.75,
            "risk": "Medium. Consumes additional RAM.",
        },
    ]

    @classmethod
    def get_enrichment(cls) -> dict[str, Any]:
        """Return full enrichment knowledge for PostgreSQL."""
        return {
            "technology": "postgresql",
            "patterns": list(cls.PATTERNS),
            "failure_scenarios": list(cls.FAILURE_SCENARIOS),
            "monitoring_requirements": list(cls.MONITORING_REQUIREMENTS),
            "adaptation_opportunities": list(cls.ADAPTATION_OPPORTUNITIES),
        }
