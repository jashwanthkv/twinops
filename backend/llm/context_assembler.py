"""
llm/context_assembler.py

WHAT IT IS DOING:
  Aggregates the complete architectural intelligence produced by AIE (Tech Profile,
  System Design Components with 3-part descriptions, Call Graph & Semantic Relationships,
  Enrichment & Operational Knobs) alongside real-time Digital Twin state and trigger
  telemetry (Isolation Forest alert or Linear Regression forecast degradation).

HOW IT IS CODED:
  Async context assembler combining:
    1. KnowledgeRetrievalService (Redis + MongoDB fallback) for static AIE knowledge.
    2. TwinStore (Redis hot state) for live component health and metric snapshots.
    3. Trigger event fields (top_metric, score, 10-min trend, forecast trajectory).
  Formats the aggregated data into a structured dictionary ready for prompt templating.

DIAGNOSTIC GUIDE:
  If LLM responses lack code references, verify that AIE completed analysis for this app_id.
  Check MongoDB databases named after the project (or default twinops_aie) for components.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from aie.persistence.services.retrieval_service import KnowledgeRetrievalService
from twin.store import TwinStore

logger = logging.getLogger(__name__)


class ContextAssembler:
    """Assembles full AIE repository intelligence and live Twin state for the Master LLM."""

    def __init__(self) -> None:
        self.retrieval = KnowledgeRetrievalService()
        self.twin_store = TwinStore()

    async def assemble_context(
        self,
        app_id: str,
        trigger_type: str,
        trigger_payload: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Assembles complete diagnostic context for Master LLM root-cause analysis.
        """
        logger.info("Assembling Master LLM diagnostic context for app_id='%s'...", app_id)

        # 1. Fetch live Digital Twin state
        twin_state = self.twin_store.load_hot(app_id)
        twin_dict: dict[str, Any] = {}
        if twin_state:
            twin_dict = {
                "health": twin_state.health,
                "active_anomalies": [
                    {"metric": a.metric, "score": a.score, "top_metric": a.top_metric}
                    for a in twin_state.active_anomalies
                ],
                "latest_metrics": {
                    k: {"value": v.value, "unit": v.unit}
                    for k, v in twin_state.metrics.items()
                },
                "forecast": twin_state.forecast,
            }

        # 2. Fetch AIE Components & Descriptions (WHAT IT IS DOING / HOW IT IS CODED / DIAGNOSTIC GUIDE)
        raw_components = await self.retrieval.get_all_components(app_id)
        components_summary: list[dict[str, Any]] = []
        for c in raw_components:
            components_summary.append({
                "component_id": c.get("component_id"),
                "name": c.get("name"),
                "component_type": c.get("component_type"),
                "technologies": c.get("technologies", []),
                "root_paths": c.get("root_paths", []),
                "description": c.get("description", ""),
            })

        # 3. Fetch Architecture Graph & Relationships
        graph_data = await self.retrieval.get_architecture_graph(app_id)
        relationships: list[dict[str, Any]] = []
        if graph_data and "edges" in graph_data:
            for edge in graph_data.get("edges", []):
                relationships.append({
                    "source": edge.get("source"),
                    "target": edge.get("target"),
                    "relation_type": edge.get("relation_type", edge.get("label", "CALLS")),
                    "description": edge.get("description", ""),
                })
        else:
            rels = await self.retrieval.rel_repo.get_all(app_id)
            for r in rels:
                relationships.append({
                    "source": r.get("source_component_id"),
                    "target": r.get("target_component_id"),
                    "relation_type": r.get("relation_type"),
                    "description": r.get("description", ""),
                })

        # 4. Fetch Operational Knobs & Capabilities (Contract C / Enrichment)
        contract_c = await self.retrieval.get_contract_c(app_id)
        operational_knobs: list[dict[str, Any]] = []
        if contract_c and "capabilities" in contract_c:
            for cap in contract_c.get("capabilities", []):
                for knob in cap.get("operational_knobs", []):
                    operational_knobs.append(knob)

        # 5. Fetch Tech Profile
        tech_profile_doc = await self.retrieval.profile_repo.get(app_id)
        tech_profile: dict[str, Any] = {}
        if tech_profile_doc:
            tech_profile = {
                "primary_language": tech_profile_doc.get("primary_language", "Unknown"),
                "languages": tech_profile_doc.get("languages", {}),
                "frameworks": tech_profile_doc.get("frameworks", []),
                "language_primary_frameworks": tech_profile_doc.get("language_primary_frameworks", {}),
            }

        return {
            "app_id": app_id,
            "trigger_type": trigger_type,
            "trigger_data": trigger_payload,
            "tech_profile": tech_profile,
            "components": components_summary,
            "relationships": relationships,
            "operational_knobs": operational_knobs,
            "digital_twin": twin_dict,
        }
