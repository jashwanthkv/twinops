"""Evidence manager — validates and tracks evidence across components and relationships."""

from __future__ import annotations

import logging
from typing import Any

from aie.models import DiscoveredComponent, DiscoveredRelationship
from aie.validation.confidence import ConfidenceCalculator

logger = logging.getLogger(__name__)


class _ImmediateAwaitable:
    """Awaitable wrapper for synchronous methods that might be awaited."""

    def __init__(self, value: Any = None) -> None:
        self._value = value

    def __await__(self):
        async def _coro():
            return self._value
        return _coro().__await__()


class EvidenceManager:
    """Validates structural integrity, references, and evidence confidence across components and relationships."""

    def __init__(self) -> None:
        self._calculator = ConfidenceCalculator()

    def validate(
        self,
        components: list[DiscoveredComponent | Any],
        relationships: list[DiscoveredRelationship | Any],
    ) -> _ImmediateAwaitable:
        """Validate components and relationships, computing confidence scores and ensuring reference integrity.

        Can be called synchronously or awaited asynchronously.
        """
        component_ids: set[str] = set()
        for c in components:
            cid = getattr(c, "component_id", None)
            if cid:
                component_ids.add(str(cid))

        for component in components:
            name = getattr(component, "name", "Unnamed")

            # 1. Check: every component has at least 1 source_evidence
            evidences = getattr(component, "evidence", None) or getattr(component, "source_evidence", None)
            if not evidences:
                logger.warning(
                    "Component '%s' (id: %s) has no source evidence — confidence will be baseline",
                    name,
                    getattr(component, "component_id", "unknown"),
                )

            # 2. Recompute and validate confidence score within 0.0–1.0
            score = self._calculator.calculate(component)
            score = max(0.0, min(1.0, score))

            # Store on component fields
            if hasattr(component, "confidence"):
                component.confidence = score
            if hasattr(component, "confidence_score"):
                component.confidence_score = score
            if hasattr(component, "metadata") and isinstance(component.metadata, dict):
                component.metadata["confidence_score"] = score

            if score < 0.5:
                logger.warning(
                    "Component '%s' has low confidence score (%.2f)", name, score
                )

            # 3. Principle: "unknown != false"
            # If a field or property is missing or None, preserve it as None, do NOT default to False.
            # (e.g. is_service, is_database, library, etc.)
            for attr in ["is_service", "is_database", "is_library", "library", "version"]:
                if hasattr(component, attr) and getattr(component, attr) is None:
                    # Explicitly retain None — unknown does not mean False
                    pass

        # 4. Check relationship references
        for rel in relationships:
            rel_id = getattr(rel, "relationship_id", "unknown")
            source_id = getattr(rel, "source_component_id", None) or getattr(rel, "from_component", None)
            target_id = getattr(rel, "target_component_id", None) or getattr(rel, "to_component", None)

            if source_id and str(source_id) not in component_ids:
                logger.warning(
                    "Relationship %s: source component '%s' not found in component inventory",
                    rel_id,
                    source_id,
                )

            if target_id and str(target_id) not in component_ids:
                logger.warning(
                    "Relationship %s: target component '%s' not found in component inventory",
                    rel_id,
                    target_id,
                )

            # Validate confidence on relationship evidence
            rel_evidences = getattr(rel, "evidence", None)
            if rel_evidences and isinstance(rel_evidences, list):
                for ev in rel_evidences:
                    ev_conf = getattr(ev, "confidence", 1.0)
                    if hasattr(ev, "confidence") and isinstance(ev_conf, (int, float)):
                        ev.confidence = max(0.0, min(1.0, float(ev_conf)))

        logger.info(
            "Evidence validation complete: %d components, %d relationships validated.",
            len(components),
            len(relationships),
        )
        return _ImmediateAwaitable(None)
