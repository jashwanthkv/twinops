"""
aie/graph/twin_format.py — Module 11: Digital Twin Format Converter.

Converts an ArchitectureGraph into the canonical JSON format expected by the
TwinOps Digital Twin Manager.
"""

from __future__ import annotations

import logging
from typing import Any

from aie.graph.generator import ArchitectureGraph

logger = logging.getLogger(__name__)


class TwinFormatConverter:
    """Converts internal ArchitectureGraph to Digital Twin compatible format."""

    def __init__(self) -> None:
        pass

    def convert(self, graph: ArchitectureGraph) -> dict[str, Any]:
        """Convert ArchitectureGraph to Digital Twin compatible dictionary.

        Format: {"nodes": [...], "edges": [...], "version": "1.0", "generated_by": "aie"}
        """
        nodes_data: list[dict[str, Any]] = []
        for node in graph.nodes:
            node_type = (
                node.type.value if hasattr(node.type, "value") else str(node.type)
            )
            nodes_data.append(
                {
                    "id": node.id,
                    "label": node.label,
                    "type": node_type,
                    "technology": node.technology,
                    "confidence_score": node.confidence_score,
                    "metadata": node.metadata,
                }
            )

        edges_data: list[dict[str, Any]] = []
        for edge in graph.edges:
            rel_type = (
                edge.relation_type.value
                if hasattr(edge.relation_type, "value")
                else str(edge.relation_type)
            )
            edges_data.append(
                {
                    "id": edge.id,
                    "from": edge.from_node,
                    "to": edge.to_node,
                    "relation": rel_type,
                    "confidence": edge.confidence,
                    "metadata": edge.metadata,
                }
            )

        twin_payload: dict[str, Any] = {
            "version": "1.0",
            "generated_by": "aie",
            "nodes": nodes_data,
            "edges": edges_data,
            "metadata": dict(graph.metadata),
        }

        logger.info(
            "Converted ArchitectureGraph to Digital Twin format: %d nodes, %d edges",
            len(nodes_data),
            len(edges_data),
        )
        return twin_payload
