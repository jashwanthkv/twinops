"""
aie/graph/descriptor_gen.py — Module 11: Descriptor Generator.

Generates the Application Descriptor (Contract B) and inferred Capability Catalog
(Contract C) from discovered components, relationships, and technology profile.
"""

from __future__ import annotations

import logging
from typing import Any

from aie.models import (
    ComponentType,
    DiscoveredComponent,
    DiscoveredRelationship,
    RelationshipConfidence,
    TechnologyProfile,
)

logger = logging.getLogger(__name__)


class DescriptorGenerator:
    """Generates Contract B (Application Descriptor) and Contract C (Capability Catalog)."""

    def __init__(self) -> None:
        pass

    def generate_descriptor(
        self,
        app_id: str,
        components: list[DiscoveredComponent],
        relationships: list[DiscoveredRelationship],
        profile: TechnologyProfile | None = None,
    ) -> dict[str, Any]:
        """Generate Contract B Application Descriptor dictionary.

        Format matches data-connector-plan Contract B:
        {
            "application_id": ...,
            "components": [{"id": ..., "type": ..., "technology": ...}],
            "relationships": [["web", "calls", "service"], ...]
        }

        Only include CONFIRMED relationships.
        """
        # Determine primary framework and name from profile if available
        app_name = app_id
        primary_framework = None
        framework_version = None

        if profile is not None:
            if hasattr(profile, "frameworks") and profile.frameworks:
                primary_framework = profile.frameworks[0].name
                framework_version = profile.frameworks[0].version
            app_name = getattr(profile, "app_name", None) or app_id

        # Map component IDs to names for human-readable Contract B relationship triples
        id_to_name: dict[str, str] = {}
        descriptor_components: list[dict[str, Any]] = []

        for comp in components:
            comp_label = comp.name if comp.name else comp.component_id
            id_to_name[comp.component_id] = comp_label

            # Primary technology
            tech = "unknown"
            if comp.technologies:
                tech = comp.technologies[0]
            elif comp.primary_language:
                tech = comp.primary_language

            type_str = (
                comp.component_type.value
                if hasattr(comp.component_type, "value")
                else str(comp.component_type)
            )

            comp_dict: dict[str, Any] = {
                "id": comp_label,
                "component_id": comp.component_id,
                "type": type_str,
                "technology": tech,
                "technologies": comp.technologies,
                "primary_language": comp.primary_language,
                "description": comp.description or "",
            }
            if comp.exposed_ports:
                comp_dict["exposed_ports"] = comp.exposed_ports
            if comp.root_paths:
                comp_dict["root_paths"] = comp.root_paths

            descriptor_components.append(comp_dict)

        # Filter for only CONFIRMED relationships and format as [source, relation, target]
        confirmed_relationships: list[list[str]] = []
        for rel in relationships:
            # Check confirmation status
            is_confirmed = False
            conf_val = rel.confidence
            if isinstance(conf_val, RelationshipConfidence):
                if conf_val in (RelationshipConfidence.CONFIRMED, RelationshipConfidence.HIGH, RelationshipConfidence.MEDIUM):
                    is_confirmed = True
            elif str(conf_val).lower() in ("high", "medium", "confirmed"):
                is_confirmed = True

            if is_confirmed:
                src = id_to_name.get(rel.source_component_id, rel.source_component_id)
                tgt = id_to_name.get(rel.target_component_id, rel.target_component_id)
                rel_type = (
                    rel.relation_type.value
                    if hasattr(rel.relation_type, "value")
                    else str(rel.relation_type)
                )
                confirmed_relationships.append([src, rel_type, tgt])

        # Extract current configuration if discovered
        current_config: dict[str, Any] = {}
        for comp in components:
            comp_label = id_to_name.get(comp.component_id, comp.name)
            cfg = comp.metadata.get("configuration") or comp.metadata.get("current_config")
            if cfg:
                current_config[comp_label] = cfg

        descriptor: dict[str, Any] = {
            "application_id": app_id,
            "name": app_name,
            "framework": primary_framework,
            "version": framework_version,
            "components": descriptor_components,
            "relationships": confirmed_relationships,
            "current_config": current_config,
        }

        logger.info(
            "Contract B descriptor generated for app '%s' with %d components and %d confirmed relationships",
            app_id,
            len(descriptor_components),
            len(confirmed_relationships),
        )
        return descriptor

    def infer_capabilities(
        self,
        components: list[DiscoveredComponent],
        app_id: str | None = None,
    ) -> dict[str, Any]:
        """Infer Contract C Capability Catalog from discovered components.

        Only includes adaptation opportunities with confidence >= 0.7 and reversible=True.
        """
        actions: list[dict[str, Any]] = []
        target_app_id = app_id or (components[0].run_id if components else "unknown")

        for comp in components:
            target_name = comp.name if comp.name else comp.component_id

            # 1. Process explicit adaptation opportunities from component metadata
            explicit_opps = (
                comp.metadata.get("adaptation_opportunities")
                or comp.metadata.get("adaptations")
                or []
            )

            found_explicit = False
            for opp in explicit_opps:
                if isinstance(opp, dict):
                    confidence = opp.get("confidence", comp.confidence)
                    reversible = opp.get("reversible", True)

                    if confidence >= 0.7 and reversible:
                        found_explicit = True
                        action_def: dict[str, Any] = {
                            "action": opp.get("action", "unknown_action"),
                            "target": opp.get("target", target_name),
                            "constraints": opp.get(
                                "constraints",
                                {
                                    "min": opp.get("min", 1),
                                    "max": opp.get("max", 100),
                                    "max_delta": opp.get("max_delta", 10),
                                },
                            ),
                            "reversible": True,
                            "requires_restart": opp.get("requires_restart", False),
                            "cooldown_override_seconds": opp.get("cooldown_override_seconds"),
                            "confidence": round(float(confidence), 3),
                        }
                        actions.append(action_def)

            # 2. If no explicit opportunities, infer heuristic opportunities based on component type and tech
            if not found_explicit and comp.confidence >= 0.7:
                techs_lower = [t.lower() for t in comp.technologies]
                name_lower = comp.name.lower()

                # Connection pool adaptations (HikariCP, pg.Pool, SQLAlchemy, DB pool)
                is_pool = (
                    any(
                        p in t
                        for t in techs_lower
                        for p in ("hikaricp", "pool", "sqlalchemy", "dbcp", "psycopg")
                    )
                    or "pool" in name_lower
                    or comp.component_type == ComponentType.DATABASE
                )
                if is_pool:
                    actions.append(
                        {
                            "action": "resize_connection_pool",
                            "target": target_name,
                            "constraints": {
                                "min": 5,
                                "max": 50,
                                "max_delta": 5,
                            },
                            "reversible": True,
                            "requires_restart": False,
                            "cooldown_override_seconds": 60,
                            "confidence": round(float(comp.confidence), 3),
                        }
                    )

                # Cache adaptations (Redis, Memcached, Caffeine)
                is_cache = (
                    any(
                        c in t
                        for t in techs_lower
                        for c in ("redis", "memcached", "caffeine", "cache")
                    )
                    or "cache" in name_lower
                    or comp.component_type == ComponentType.CACHE
                )
                if is_cache:
                    actions.append(
                        {
                            "action": "adjust_cache_ttl",
                            "target": target_name,
                            "constraints": {
                                "min": 60,
                                "max": 3600,
                                "max_delta": 300,
                            },
                            "reversible": True,
                            "requires_restart": False,
                            "cooldown_override_seconds": 60,
                            "confidence": round(float(comp.confidence), 3),
                        }
                    )

                # Worker concurrency adaptations
                is_worker = (
                    comp.component_type == ComponentType.WORKER
                    or any(w in t for t in techs_lower for w in ("celery", "bull", "worker"))
                    or "worker" in name_lower
                )
                if is_worker:
                    actions.append(
                        {
                            "action": "scale_worker_concurrency",
                            "target": target_name,
                            "constraints": {
                                "min": 1,
                                "max": 20,
                                "max_delta": 2,
                            },
                            "reversible": True,
                            "requires_restart": False,
                            "cooldown_override_seconds": 120,
                            "confidence": round(float(comp.confidence), 3),
                        }
                    )

        catalog: dict[str, Any] = {
            "application_id": target_app_id,
            "actions": actions,
        }

        logger.info(
            "Contract C Capability Catalog inferred for app '%s' with %d adaptation actions",
            target_app_id,
            len(actions),
        )
        return catalog
