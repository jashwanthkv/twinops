"""Technical knowledge enricher.

Enriches discovered components with curated operational, failure scenario,
monitoring, and adaptation patterns based on detected technologies.
"""

from __future__ import annotations

import inspect
import logging
from typing import Any

from aie.enrichment.knowledge_modules.postgresql import PostgreSQLKnowledge
from aie.enrichment.knowledge_modules.redis import RedisKnowledge
from aie.enrichment.knowledge_modules.sqlalchemy import SQLAlchemyKnowledge
from aie.models import ComponentType, DiscoveredComponent

logger = logging.getLogger(__name__)

TECH_KNOWLEDGE_MAP: dict[str, type] = {
    "redis": RedisKnowledge,
    "postgresql": PostgreSQLKnowledge,
    "postgres": PostgreSQLKnowledge,
    "psycopg2": PostgreSQLKnowledge,
    "asyncpg": PostgreSQLKnowledge,
    "sqlalchemy": SQLAlchemyKnowledge,
}


class _ImmediateAwaitable:
    """Awaitable wrapper that allows a synchronous result to be optionally awaited."""

    def __init__(self, value: Any = None) -> None:
        self._value = value

    def __await__(self):
        async def _coro():
            return self._value
        return _coro().__await__()


class TechnicalEnricher:
    """Enriches DiscoveredComponent instances in place with domain-specific knowledge."""

    def __init__(self) -> None:
        self._modules: dict[str, Any] = {
            k: v() if inspect.isclass(v) else v
            for k, v in TECH_KNOWLEDGE_MAP.items()
        }

    def enrich(
        self,
        components: list[DiscoveredComponent | Any],
        profile: Any = None,
    ) -> _ImmediateAwaitable:
        """Enrich components in place by appending tech-specific operational knowledge.

        Matches component technologies, types, and names to specialized knowledge modules.
        Appends failure scenarios, monitoring requirements, and adaptation opportunities.
        Can be invoked synchronously or awaited via async.
        """
        if not components:
            return _ImmediateAwaitable(None)

        logger.info("Enriching %d components with technical knowledge...", len(components))

        # Check global profile technologies
        profile_techs: set[str] = set()
        if profile:
            frameworks = getattr(profile, "frameworks", [])
            for fw in frameworks:
                fname = getattr(fw, "name", "")
                if fname:
                    profile_techs.add(fname.lower())
            technologies = getattr(profile, "technologies", [])
            for tech in technologies:
                tname = getattr(tech, "name", "")
                if tname:
                    profile_techs.add(tname.lower())

        for comp in components:
            matched_knowledges: list[Any] = []
            seen_modules: set[str] = set()

            # Gather tech keywords from component
            candidate_keys: list[str] = []

            # Check technologies list
            comp_techs = getattr(comp, "technologies", [])
            if isinstance(comp_techs, list):
                candidate_keys.extend(str(t).lower() for t in comp_techs)

            # Check technology string
            comp_tech = getattr(comp, "technology", None)
            if comp_tech:
                candidate_keys.append(str(comp_tech).lower())

            # Check library string
            comp_lib = getattr(comp, "library", None)
            if comp_lib:
                candidate_keys.append(str(comp_lib).lower())

            # Check name and primary language
            comp_name = getattr(comp, "name", "")
            if comp_name:
                candidate_keys.append(comp_name.lower())

            primary_lang = getattr(comp, "primary_language", None)
            if primary_lang:
                candidate_keys.append(str(primary_lang).lower())

            # Check component_type
            comp_type = getattr(comp, "component_type", None)
            if comp_type == ComponentType.CACHE or comp_type == "cache":
                candidate_keys.append("redis")
            elif comp_type == ComponentType.DATABASE or comp_type == "database":
                candidate_keys.extend(["postgresql", "sqlalchemy"])

            for key in candidate_keys:
                for mod_key, mod_instance in self._modules.items():
                    if mod_key in key and mod_key not in seen_modules:
                        matched_knowledges.append(mod_instance)
                        seen_modules.add(mod_key)

            # If no direct match, check profile techs
            for ptech in profile_techs:
                for mod_key, mod_instance in self._modules.items():
                    if mod_key in ptech and mod_key not in seen_modules:
                        if (
                            "redis" in mod_key
                            and comp_type in (ComponentType.CACHE, ComponentType.SERVICE, "cache", "service")
                        ) or (
                            ("postgre" in mod_key or "sql" in mod_key)
                            and comp_type in (ComponentType.DATABASE, ComponentType.SERVICE, "database", "service")
                        ):
                            matched_knowledges.append(mod_instance)
                            seen_modules.add(mod_key)

            if not matched_knowledges:
                continue

            # Merge knowledge into component
            for km in matched_knowledges:
                enrichment = km.get_enrichment()
                self._merge_enrichment(comp, enrichment)

        logger.info("Technical enrichment complete.")
        return _ImmediateAwaitable(None)

    def _merge_enrichment(self, comp: Any, enrichment: dict[str, Any]) -> None:
        """Merge enrichment failure scenarios, monitoring, and adaptation opportunities into component."""
        tech_name = enrichment.get("technology", "unknown")

        # 1. Failure Scenarios
        new_failures = enrichment.get("failure_scenarios", [])
        # Check if attribute exists on component
        if hasattr(comp, "failure_scenarios") and isinstance(comp.failure_scenarios, list):
            for fs in new_failures:
                fs_name = fs.get("name") if isinstance(fs, dict) else str(fs)
                if not self._item_exists(fs_name, comp.failure_scenarios):
                    comp.failure_scenarios.append(fs)

        # Also store in metadata for persistence & API schemas
        if hasattr(comp, "metadata") and isinstance(comp.metadata, dict):
            meta_failures = comp.metadata.setdefault("failure_scenarios", [])
            for fs in new_failures:
                fs_name = fs.get("name") if isinstance(fs, dict) else str(fs)
                if not self._item_exists(fs_name, meta_failures):
                    meta_failures.append(fs)

        # 2. Monitoring Requirements
        new_monitoring = enrichment.get("monitoring_requirements", [])
        if hasattr(comp, "monitoring_requirements") and isinstance(comp.monitoring_requirements, list):
            for mr in new_monitoring:
                mr_name = mr.get("metric") if isinstance(mr, dict) else str(mr)
                if not self._item_exists(mr_name, comp.monitoring_requirements):
                    comp.monitoring_requirements.append(mr)

        if hasattr(comp, "metadata") and isinstance(comp.metadata, dict):
            meta_mon = comp.metadata.setdefault("monitoring_requirements", [])
            for mr in new_monitoring:
                mr_name = mr.get("metric") if isinstance(mr, dict) else str(mr)
                if not self._item_exists(mr_name, meta_mon):
                    meta_mon.append(mr)

        # 3. Adaptation Opportunities
        new_adaptations = enrichment.get("adaptation_opportunities", [])
        if hasattr(comp, "adaptation_opportunities") and isinstance(comp.adaptation_opportunities, list):
            for ao in new_adaptations:
                ao_name = ao.get("opportunity") if isinstance(ao, dict) else str(ao)
                if not self._item_exists(ao_name, comp.adaptation_opportunities):
                    comp.adaptation_opportunities.append(ao)

        if hasattr(comp, "metadata") and isinstance(comp.metadata, dict):
            meta_adapt = comp.metadata.setdefault("adaptation_opportunities", [])
            for ao in new_adaptations:
                ao_name = ao.get("opportunity") if isinstance(ao, dict) else str(ao)
                if not self._item_exists(ao_name, meta_adapt):
                    meta_adapt.append(ao)

            enriched_tags = comp.metadata.setdefault("enriched_technologies", [])
            if tech_name not in enriched_tags:
                enriched_tags.append(tech_name)

    def _item_exists(self, identifier: str, item_list: list[Any]) -> bool:
        if not identifier:
            return False
        ident_lower = identifier.lower()
        for item in item_list:
            if isinstance(item, str) and ident_lower in item.lower():
                return True
            if isinstance(item, dict):
                name = item.get("name") or item.get("metric") or item.get("opportunity") or ""
                if str(name).lower() == ident_lower:
                    return True
        return False
