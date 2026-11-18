"""SQLAlchemy technical knowledge module.

Provides known failure modes, diagnostic patterns, monitoring requirements,
and adaptation opportunities for SQLAlchemy ORM and engine configurations.
"""

from __future__ import annotations

from typing import Any


class SQLAlchemyKnowledge:
    """Pre-curated operational and diagnostic knowledge for SQLAlchemy."""

    PATTERNS: list[str] = [
        "unit_of_work",
        "declarative_base",
        "queue_pool",
        "scoped_session",
        "lazy_loading_n_plus_one",
    ]

    FAILURE_SCENARIOS: list[dict[str, Any]] = [
        {
            "name": "pool_overflow",
            "scenario": "Concurrent requests exceed pool_size + max_overflow, raising sqlalchemy.exc.TimeoutError.",
            "impact": "High. HTTP 500 errors on requests requiring database connections.",
            "mitigation": "Increase max_overflow, configure pool_timeout, and ensure short-lived transactions.",
            "probability": "medium",
        },
        {
            "name": "connection_leaks",
            "scenario": "Database connections checked out of QueuePool are not returned to the pool due to unhandled exceptions.",
            "impact": "Critical. Gradual exhaustion of connection pool until entire application hangs.",
            "mitigation": "Enforce context managers ('with engine.connect()' or 'with Session()') and pool_recycle.",
            "probability": "high",
        },
        {
            "name": "session_not_closed",
            "scenario": "Session instances remain open after request finishes, holding locks and memory.",
            "impact": "Medium to High. Connection holding, stale data reads, and memory bloat.",
            "mitigation": "Register dependency teardown in FastAPI/Flask ('Session.close()' or scoped_session.remove()).",
            "probability": "high",
        },
        {
            "name": "autoflush_conflicts",
            "scenario": "Automatic session flush triggers unexpected SQL emission before an explicit commit, encountering constraint violations.",
            "impact": "Medium. Premature IntegrityError or detached instance errors.",
            "mitigation": "Explicitly manage autoflush (`session.no_autoflush`) during complex bulk operations.",
            "probability": "low",
        },
    ]

    MONITORING_REQUIREMENTS: list[dict[str, Any]] = [
        {
            "metric": "pool_size",
            "type": "gauge",
            "description": "Configured base number of connections retained in the QueuePool.",
            "threshold": "none",
        },
        {
            "metric": "pool_checked_out",
            "type": "gauge",
            "description": "Number of connections currently checked out by application threads.",
            "threshold": "pool_checked_out / (pool_size + max_overflow) > 0.80",
        },
        {
            "metric": "pool_overflow",
            "type": "gauge",
            "description": "Number of additional connections created beyond pool_size.",
            "threshold": "pool_overflow > 0.9 * max_overflow",
        },
        {
            "metric": "pool_invalid",
            "type": "counter",
            "description": "Count of invalid or dropped connections detected by pool_recycle / ping.",
            "threshold": "rate(pool_invalid) > 1/min",
        },
    ]

    ADAPTATION_OPPORTUNITIES: list[dict[str, Any]] = [
        {
            "opportunity": "Dynamic pool_recycle tuning",
            "action": "Lower pool_recycle time to prevent stale disconnected sockets across firewall reboots.",
            "reversible": True,
            "confidence": 0.85,
            "risk": "Low. Minimal overhead of reconnecting.",
        },
        {
            "opportunity": "QueuePool max_overflow burst buffer",
            "action": "Temporarily allow higher overflow connections during brief traffic spikes.",
            "reversible": True,
            "confidence": 0.80,
            "risk": "Low.",
        },
    ]

    @classmethod
    def get_enrichment(cls) -> dict[str, Any]:
        """Return full enrichment knowledge for SQLAlchemy."""
        return {
            "technology": "sqlalchemy",
            "patterns": list(cls.PATTERNS),
            "failure_scenarios": list(cls.FAILURE_SCENARIOS),
            "monitoring_requirements": list(cls.MONITORING_REQUIREMENTS),
            "adaptation_opportunities": list(cls.ADAPTATION_OPPORTUNITIES),
        }
