"""Main relationship analyzer for discovering architectural connections between components."""

from __future__ import annotations

import logging
from typing import Any

from aie.models import (
    DiscoveredComponent,
    DiscoveredRelationship,
    RelationshipConfidence,
)
from aie.relationships.call_graph import CallRelationshipBuilder
from aie.relationships.import_graph import ImportRelationshipBuilder
from aie.static_analysis.ir import CodebaseIR

logger = logging.getLogger(__name__)

# Numeric rank for confidence tiers: higher means more confident
_CONFIDENCE_RANK: dict[str, int] = {
    RelationshipConfidence.CONFIRMED.value: 5,
    RelationshipConfidence.HIGH.value: 4,
    RelationshipConfidence.MEDIUM.value: 3,
    RelationshipConfidence.LOW.value: 2,
    RelationshipConfidence.INFERRED.value: 1,
}


class RelationshipAnalyzer:
    """Analyzes and aggregates relationships between components."""

    def __init__(self) -> None:
        self.import_builder = ImportRelationshipBuilder()
        self.call_builder = CallRelationshipBuilder()

    def analyze(
        self,
        components: list[DiscoveredComponent],
        codebase_ir: CodebaseIR | Any,
    ) -> list[DiscoveredRelationship]:
        """Discover, merge, and deduplicate relationships between components.

        Runs import and call graph builders.
        Deduplicates relationships sharing the same (source, target, relation_type)
        by retaining the one with highest confidence and merging their evidence.
        Excludes self-relationships where source == target.
        """
        if not components or not codebase_ir:
            return []

        logger.info(
            "Analyzing relationships across %d components...", len(components)
        )

        all_rels: list[DiscoveredRelationship] = []

        # 1. Run import graph analysis
        try:
            import_rels = self.import_builder.build(components, codebase_ir)
            all_rels.extend(import_rels)
            logger.info("Import analyzer found %d relationships", len(import_rels))
        except Exception as exc:
            logger.warning("Error running import relationship builder: %s", exc)

        # 2. Run call graph analysis
        try:
            call_rels = self.call_builder.build(components, codebase_ir)
            all_rels.extend(call_rels)
            logger.info("Call analyzer found %d relationships", len(call_rels))
        except Exception as exc:
            logger.warning("Error running call relationship builder: %s", exc)

        # 3. Filter out self-relationships and deduplicate
        deduped: dict[tuple[str, str, str], DiscoveredRelationship] = {}

        for rel in all_rels:
            # Exclude self-relationships
            if rel.source_component_id == rel.target_component_id:
                continue

            key = (
                rel.source_component_id,
                rel.target_component_id,
            )

            if key not in deduped:
                deduped[key] = rel
            else:
                existing = deduped[key]
                existing_rank = _CONFIDENCE_RANK.get(
                    existing.confidence.value, 0
                )
                new_rank = _CONFIDENCE_RANK.get(rel.confidence.value, 0)

                # Merge evidence
                merged_evidence = list(existing.evidence)
                for ev in rel.evidence:
                    if not any(
                        e.source_path == ev.source_path
                        and e.snippet == ev.snippet
                        for e in merged_evidence
                    ):
                        merged_evidence.append(ev)

                if new_rank > existing_rank:
                    # Update with higher confidence relationship, keep combined evidence
                    rel.evidence = merged_evidence
                    deduped[key] = rel
                else:
                    existing.evidence = merged_evidence

        result = list(deduped.values())
        logger.info(
            "Relationship analysis completed: %d unique relationships found",
            len(result),
        )
        return result
