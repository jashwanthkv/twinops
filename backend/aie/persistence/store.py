"""
aie/persistence/store.py — Module 12: Knowledge Persistence Store.

In-memory knowledge persistence for all AIE data artifacts using Python dictionaries.
No SQLite, no disk I/O.
"""

from __future__ import annotations

import logging
from typing import Any

from aie.graph.generator import ArchitectureGraph
from aie.models import (
    AnalysisRun,
    DiscoveredComponent,
    DiscoveredRelationship,
    FileEntry,
    TechnologyProfile,
)

logger = logging.getLogger(__name__)


class AIEStore:
    """In-memory store for AIE analyses, components, graphs, and catalogs."""

    def __init__(self) -> None:
        self._analyses: dict[str, AnalysisRun] = {}
        self._components: dict[str, list[DiscoveredComponent]] = {}  # keyed by app_id / run_id
        self._relationships: dict[str, list[DiscoveredRelationship]] = {}  # keyed by app_id / run_id
        self._graphs: dict[str, ArchitectureGraph] = {}  # keyed by app_id / run_id
        self._descriptors: dict[str, dict[str, Any]] = {}  # keyed by app_id / run_id
        self._capabilities: dict[str, dict[str, Any]] = {}  # keyed by app_id / run_id
        self._profiles: dict[str, TechnologyProfile] = {}  # keyed by app_id / run_id
        self._file_inventories: dict[str, list[FileEntry]] = {}  # keyed by analysis_id

    # ──────────────────────────────────────────────────────────────────────── #
    # Analysis Run Operations
    # ──────────────────────────────────────────────────────────────────────── #

    def store_analysis(self, run: AnalysisRun) -> AnalysisRun:
        """Store or update an AnalysisRun."""
        self._analyses[run.run_id] = run
        logger.debug("Stored analysis run %s (status: %s)", run.run_id, run.status)
        return run

    def update_analysis(self, run: AnalysisRun) -> AnalysisRun:
        """Update an AnalysisRun."""
        return self.store_analysis(run)

    def get_analysis(self, analysis_id: str) -> AnalysisRun | None:
        """Retrieve an AnalysisRun by ID."""
        return self._analyses.get(analysis_id)

    def list_analyses(self) -> list[AnalysisRun]:
        """List all analysis runs, newest first."""
        return sorted(
            self._analyses.values(),
            key=lambda r: getattr(r, "created_at", None) or "",
            reverse=True,
        )

    # ──────────────────────────────────────────────────────────────────────── #
    # Components Operations
    # ──────────────────────────────────────────────────────────────────────── #

    def store_components(self, app_id: str, components: list[DiscoveredComponent]) -> None:
        """Store discovered components for an application."""
        self._components[app_id] = list(components)
        logger.debug("Stored %d components for app %s", len(components), app_id)

    def get_components(self, app_id: str) -> list[DiscoveredComponent]:
        """Retrieve discovered components for an application or run ID."""
        if app_id in self._components:
            return self._components[app_id]
        return []

    def get_component(self, app_id: str, component_id: str) -> DiscoveredComponent | None:
        """Retrieve a specific discovered component by ID."""
        comps = self.get_components(app_id)
        for c in comps:
            if getattr(c, "component_id", None) == component_id or getattr(c, "name", None) == component_id:
                return c
        return None

    # ──────────────────────────────────────────────────────────────────────── #
    # Relationships Operations
    # ──────────────────────────────────────────────────────────────────────── #

    def store_relationships(
        self, app_id: str, relationships: list[DiscoveredRelationship]
    ) -> None:
        """Store discovered relationships for an application."""
        self._relationships[app_id] = list(relationships)
        logger.debug("Stored %d relationships for app %s", len(relationships), app_id)

    def get_relationships(self, app_id: str) -> list[DiscoveredRelationship]:
        """Retrieve discovered relationships for an application or run ID."""
        return self._relationships.get(app_id, [])

    # ──────────────────────────────────────────────────────────────────────── #
    # Architecture Graph Operations
    # ──────────────────────────────────────────────────────────────────────── #

    def store_graph(self, app_id: str, graph: ArchitectureGraph) -> None:
        """Store generated ArchitectureGraph for an application."""
        self._graphs[app_id] = graph
        logger.debug("Stored architecture graph for app %s", app_id)

    def get_graph(self, app_id: str) -> ArchitectureGraph | None:
        """Retrieve ArchitectureGraph for an application or run ID."""
        return self._graphs.get(app_id)

    # ──────────────────────────────────────────────────────────────────────── #
    # Contract B Descriptor Operations
    # ──────────────────────────────────────────────────────────────────────── #

    def store_descriptor(self, app_id: str, descriptor: dict[str, Any]) -> None:
        """Store Application Descriptor (Contract B) for an application."""
        self._descriptors[app_id] = descriptor
        logger.debug("Stored Contract B descriptor for app %s", app_id)

    def get_descriptor(self, app_id: str) -> dict[str, Any] | None:
        """Retrieve Application Descriptor (Contract B) for an application or run ID."""
        return self._descriptors.get(app_id)

    # ──────────────────────────────────────────────────────────────────────── #
    # Contract C Capabilities Operations
    # ──────────────────────────────────────────────────────────────────────── #

    def store_capabilities(self, app_id: str, capabilities: dict[str, Any]) -> None:
        """Store Capability Catalog (Contract C) for an application."""
        self._capabilities[app_id] = capabilities
        logger.debug("Stored Contract C capabilities for app %s", app_id)

    def get_capabilities(self, app_id: str) -> dict[str, Any] | None:
        """Retrieve Capability Catalog (Contract C) for an application or run ID."""
        return self._capabilities.get(app_id)

    # ──────────────────────────────────────────────────────────────────────── #
    # Technology Profile Operations
    # ──────────────────────────────────────────────────────────────────────── #

    def store_profile(self, app_id: str, profile: TechnologyProfile) -> None:
        """Store TechnologyProfile for an application."""
        self._profiles[app_id] = profile
        logger.debug("Stored technology profile for app %s", app_id)

    def get_profile(self, app_id: str) -> TechnologyProfile | None:
        """Retrieve TechnologyProfile for an application or run ID."""
        return self._profiles.get(app_id)

    # ──────────────────────────────────────────────────────────────────────── #
    # File Inventory Operations
    # ──────────────────────────────────────────────────────────────────────── #

    def store_file_inventory(self, analysis_id: str, files: list[FileEntry]) -> None:
        """Store file inventory for an analysis run."""
        self._file_inventories[analysis_id] = list(files)
        logger.debug("Stored %d files for analysis %s", len(files), analysis_id)

    def get_file_inventory(self, analysis_id: str) -> list[FileEntry]:
        """Retrieve file inventory for an analysis run or app ID."""
        return self._file_inventories.get(analysis_id, [])

    # ──────────────────────────────────────────────────────────────────────── #
    # Atomic Batch Store
    # ──────────────────────────────────────────────────────────────────────── #

    def store_all(
        self,
        run: AnalysisRun,
        inventory: list[FileEntry],
        profile: TechnologyProfile,
        components: list[DiscoveredComponent],
        relationships: list[DiscoveredRelationship],
        graph: ArchitectureGraph,
        descriptor: dict[str, Any],
        capabilities: dict[str, Any],
    ) -> None:
        """Store all outputs of an analysis run atomically in the in-memory store."""
        run_id = run.run_id
        app_id = descriptor.get("application_id") or run_id

        # 1. Store run
        self.store_analysis(run)

        # 2. Store file inventory under run_id and app_id
        self.store_file_inventory(run_id, inventory)
        if app_id != run_id:
            self.store_file_inventory(app_id, inventory)

        # 3. Store profile under run_id and app_id
        self.store_profile(run_id, profile)
        if app_id != run_id:
            self.store_profile(app_id, profile)

        # 4. Store components under run_id and app_id
        self.store_components(run_id, components)
        if app_id != run_id:
            self.store_components(app_id, components)

        # 5. Store relationships under run_id and app_id
        self.store_relationships(run_id, relationships)
        if app_id != run_id:
            self.store_relationships(app_id, relationships)

        # 6. Store graph under run_id and app_id
        self.store_graph(run_id, graph)
        if app_id != run_id:
            self.store_graph(app_id, graph)

        # 7. Store descriptor under run_id and app_id
        self.store_descriptor(run_id, descriptor)
        if app_id != run_id:
            self.store_descriptor(app_id, descriptor)

        # 8. Store capabilities under run_id and app_id
        self.store_capabilities(run_id, capabilities)
        if app_id != run_id:
            self.store_capabilities(app_id, capabilities)

        logger.info(
            "Persisted all analysis artifacts for run '%s' (app '%s')",
            run_id,
            app_id,
        )


# Global singleton instance
aie_store = AIEStore()
