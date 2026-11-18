"""
aie/graph/generator.py — Module 11: Architecture Graph Generator.

Constructs an ArchitectureGraph containing typed nodes and directed edges from
discovered components and relationships.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from aie.models import (
    ComponentType,
    DiscoveredComponent,
    DiscoveredRelationship,
    RelationType,
    RelationshipConfidence,
)

logger = logging.getLogger(__name__)


class GraphNode(BaseModel):
    """A node in the architecture graph representing a software component."""

    model_config = ConfigDict(populate_by_name=True)

    id: str = Field(description="Unique component identifier")
    label: str = Field(description="Human-readable component name")
    type: ComponentType | str = Field(
        default=ComponentType.UNKNOWN,
        description="Semantic type of the component",
    )
    technology: str = Field(
        default="unknown",
        description="Primary technology or framework used by the component",
    )
    confidence_score: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description="Confidence score of component detection",
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Additional component metadata",
    )


class GraphEdge(BaseModel):
    """A directed edge in the architecture graph representing a relationship."""

    model_config = ConfigDict(populate_by_name=True)

    id: str = Field(description="Unique edge identifier")
    from_node: str = Field(description="Source component ID")
    to_node: str = Field(description="Target component ID")
    relation_type: RelationType | str = Field(
        default=RelationType.CALLS,
        description="Type of relationship",
    )
    confidence: str = Field(
        default="confirmed",
        description="Confidence tier: 'confirmed' or 'inferred'",
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Relationship evidence and attributes",
    )


class ArchitectureGraph(BaseModel):
    """Complete architecture graph for an application."""

    model_config = ConfigDict(populate_by_name=True)

    nodes: list[GraphNode] = Field(default_factory=list)
    edges: list[GraphEdge] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ArchitectureGraphGenerator:
    """Generates an ArchitectureGraph from discovered components and relationships."""

    def __init__(self) -> None:
        pass

    def generate(
        self,
        components: list[DiscoveredComponent],
        relationships: list[DiscoveredRelationship],
    ) -> ArchitectureGraph:
        """Build an ArchitectureGraph from components (nodes) and relationships (edges)."""
        nodes: list[GraphNode] = []
        edges: list[GraphEdge] = []

        # Build nodes from components
        for comp in components:
            # Determine primary technology representation
            tech = "unknown"
            if comp.technologies:
                tech = comp.technologies[0]
            elif comp.primary_language:
                tech = comp.primary_language

            node_metadata: dict[str, Any] = {
                "run_id": comp.run_id,
                "primary_language": comp.primary_language,
                "technologies": comp.technologies,
                "root_paths": comp.root_paths,
                "exposed_ports": comp.exposed_ports,
                "environment_variables": comp.environment_variables,
                "description": comp.description,
            }
            if comp.metadata:
                node_metadata.update(comp.metadata)

            node = GraphNode(
                id=comp.component_id,
                label=comp.name,
                type=comp.component_type,
                technology=tech,
                confidence_score=comp.confidence,
                metadata=node_metadata,
            )
            nodes.append(node)

        # Build edges from relationships
        for rel in relationships:
            # Determine confidence label: 'confirmed' or 'inferred'
            conf_val = rel.confidence
            if isinstance(conf_val, RelationshipConfidence):
                conf_str = (
                    "confirmed"
                    if conf_val in (RelationshipConfidence.HIGH, RelationshipConfidence.MEDIUM)
                    else "inferred"
                )
            else:
                conf_str = (
                    "confirmed"
                    if str(conf_val).lower() in ("high", "medium", "confirmed")
                    else "inferred"
                )

            rel_type = (
                rel.relation_type.value
                if hasattr(rel.relation_type, "value")
                else str(rel.relation_type)
            )

            edge_metadata: dict[str, Any] = {
                "run_id": rel.run_id,
                "confidence_tier": (
                    rel.confidence.value
                    if hasattr(rel.confidence, "value")
                    else str(rel.confidence)
                ),
                "description": rel.description,
                "evidence_count": len(rel.evidence),
            }
            if rel.metadata:
                edge_metadata.update(rel.metadata)

            edge = GraphEdge(
                id=rel.relationship_id,
                from_node=rel.source_component_id,
                to_node=rel.target_component_id,
                relation_type=rel_type,
                confidence=conf_str,
                metadata=edge_metadata,
            )
            edges.append(edge)

        graph_metadata: dict[str, Any] = {
            "node_count": len(nodes),
            "edge_count": len(edges),
            "generated_at": datetime.now(tz=timezone.utc).isoformat(),
        }

        logger.info(
            "ArchitectureGraph generated with %d nodes and %d edges",
            len(nodes),
            len(edges),
        )
        return ArchitectureGraph(nodes=nodes, edges=edges, metadata=graph_metadata)
