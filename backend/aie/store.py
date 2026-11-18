"""
aie/store.py — Global in-memory runtime store for all AIE data.

Everything lives in plain Python dicts keyed by their primary ID string.
No SQLite, no external DB — all state is lost on process restart (by design
for this phase).

Exported symbols
----------------
runs            : dict[run_id, AnalysisRun]
file_entries    : dict[run_id, list[FileEntry]]
tech_profiles   : dict[run_id, TechnologyProfile]
components      : dict[run_id, list[DiscoveredComponent]]
relationships   : dict[run_id, list[DiscoveredRelationship]]
run_logs        : dict[run_id, list[str]]  — append-only text log lines

Helper functions are also provided for common CRUD patterns so that the rest
of the codebase doesn't have to repeat the same boilerplate.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from aie.models import (
    AnalysisRun,
    DiscoveredComponent,
    DiscoveredRelationship,
    FileEntry,
    TechnologyProfile,
)
from aie.persistence.store import AIEStore, aie_store

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────────── #
# Primary data stores
# ──────────────────────────────────────────────────────────────────────────── #

# Key: run_id (str)  →  Value: AnalysisRun
runs: dict[str, AnalysisRun] = {}

# Key: run_id (str)  →  Value: ordered list of FileEntry objects
file_entries: dict[str, list[FileEntry]] = {}

# Key: run_id (str)  →  Value: TechnologyProfile
tech_profiles: dict[str, TechnologyProfile] = {}

# Key: run_id (str)  →  Value: list of DiscoveredComponent objects
components: dict[str, list[DiscoveredComponent]] = {}

# Key: run_id (str)  →  Value: list of DiscoveredRelationship objects
relationships: dict[str, list[DiscoveredRelationship]] = {}

# Key: run_id / app_id (str)  →  Value: ArchitectureGraph object or dict
graphs: dict[str, Any] = {}

# Key: run_id / app_id (str)  →  Value: Contract B application descriptor dict
descriptors: dict[str, dict] = {}

# Key: run_id / app_id (str)  →  Value: Contract C capability catalog dict
capabilities: dict[str, dict] = {}

# Key: run_id (str)  →  Value: list of plain-text log lines (append-only)
run_logs: dict[str, list[str]] = {}


# ──────────────────────────────────────────────────────────────────────────── #
# AnalysisRun helpers
# ──────────────────────────────────────────────────────────────────────────── #


def create_run(run: AnalysisRun) -> AnalysisRun:
    """Register a new AnalysisRun in the store and initialise its sub-stores."""
    if run.run_id in runs:
        raise KeyError(f"Run {run.run_id} already exists")
    runs[run.run_id] = run
    file_entries[run.run_id] = []
    components[run.run_id] = []
    relationships[run.run_id] = []
    run_logs[run.run_id] = []
    logger.debug("store: created run %s", run.run_id)
    return run


def get_run(run_id: str) -> AnalysisRun | None:
    """Return the AnalysisRun for *run_id*, or None if not found."""
    return runs.get(run_id)


def update_run(run_id: str, **fields: Any) -> AnalysisRun:
    """Update arbitrary fields on an existing AnalysisRun in-place.

    Raises KeyError if the run does not exist.
    Returns the updated run.
    """
    run = runs.get(run_id)
    if run is None:
        raise KeyError(f"Run {run_id} not found")
    updated = run.model_copy(update=fields)
    runs[run_id] = updated
    return updated


def set_run_status(run_id: str, status: str, error_message: str | None = None) -> AnalysisRun:
    """Convenience wrapper to transition a run to a new status."""
    now = datetime.now(tz=timezone.utc)
    extra: dict[str, Any] = {"status": status}
    if status == "analysing" and runs[run_id].started_at is None:
        extra["started_at"] = now
    if status in ("complete", "failed"):
        extra["completed_at"] = now
    if error_message is not None:
        extra["error_message"] = error_message
    return update_run(run_id, **extra)


def list_runs() -> list[AnalysisRun]:
    """Return all runs, newest first."""
    return sorted(runs.values(), key=lambda r: r.created_at, reverse=True)


def delete_run(run_id: str) -> bool:
    """Remove all data associated with *run_id*. Returns True if found."""
    if run_id not in runs:
        return False
    runs.pop(run_id, None)
    file_entries.pop(run_id, None)
    tech_profiles.pop(run_id, None)
    components.pop(run_id, None)
    relationships.pop(run_id, None)
    run_logs.pop(run_id, None)
    logger.debug("store: deleted run %s", run_id)
    return True


# ──────────────────────────────────────────────────────────────────────────── #
# FileEntry helpers
# ──────────────────────────────────────────────────────────────────────────── #


def add_file_entries(run_id: str, entries: list[FileEntry]) -> None:
    """Append *entries* to the file list for *run_id*."""
    if run_id not in file_entries:
        file_entries[run_id] = []
    file_entries[run_id].extend(entries)


def get_file_entries(run_id: str) -> list[FileEntry]:
    """Return all FileEntry objects for *run_id*."""
    return file_entries.get(run_id, [])


# ──────────────────────────────────────────────────────────────────────────── #
# TechnologyProfile helpers
# ──────────────────────────────────────────────────────────────────────────── #


def set_tech_profile(profile: TechnologyProfile) -> TechnologyProfile:
    """Store (or replace) the TechnologyProfile for a run."""
    tech_profiles[profile.run_id] = profile
    return profile


def get_tech_profile(run_id: str) -> TechnologyProfile | None:
    """Return the TechnologyProfile for *run_id*, or None."""
    return tech_profiles.get(run_id)


# ──────────────────────────────────────────────────────────────────────────── #
# DiscoveredComponent helpers
# ──────────────────────────────────────────────────────────────────────────── #


def add_component(component: DiscoveredComponent) -> DiscoveredComponent:
    """Add a DiscoveredComponent to its run's list."""
    if component.run_id not in components:
        components[component.run_id] = []
    components[component.run_id].append(component)
    return component


def get_components(run_id: str) -> list[DiscoveredComponent]:
    """Return all DiscoveredComponent objects for *run_id*."""
    return components.get(run_id, [])


def get_component_by_id(run_id: str, component_id: str) -> DiscoveredComponent | None:
    """Look up a single component by its ID within a run."""
    for c in components.get(run_id, []):
        if c.component_id == component_id:
            return c
    return None


# ──────────────────────────────────────────────────────────────────────────── #
# DiscoveredRelationship helpers
# ──────────────────────────────────────────────────────────────────────────── #


def add_relationship(rel: DiscoveredRelationship) -> DiscoveredRelationship:
    """Add a DiscoveredRelationship to its run's list."""
    if rel.run_id not in relationships:
        relationships[rel.run_id] = []
    relationships[rel.run_id].append(rel)
    return rel


def get_relationships(run_id: str) -> list[DiscoveredRelationship]:
    """Return all DiscoveredRelationship objects for *run_id*."""
    return relationships.get(run_id, [])


# ──────────────────────────────────────────────────────────────────────────── #
# Log helpers
# ──────────────────────────────────────────────────────────────────────────── #


def append_log(run_id: str, line: str) -> None:
    """Append a plain-text log line for *run_id*."""
    if run_id not in run_logs:
        run_logs[run_id] = []
    ts = datetime.now(tz=timezone.utc).isoformat()
    run_logs[run_id].append(f"[{ts}] {line}")


def get_logs(run_id: str) -> list[str]:
    """Return all log lines for *run_id*."""
    return run_logs.get(run_id, [])


# ──────────────────────────────────────────────────────────────────────────── #
# Global summary
# ──────────────────────────────────────────────────────────────────────────── #


def store_summary() -> dict[str, Any]:
    """Return a lightweight summary of current store contents (for health checks)."""
    return {
        "total_runs": len(runs),
        "runs_by_status": _count_by_status(),
        "total_components": sum(len(v) for v in components.values()),
        "total_relationships": sum(len(v) for v in relationships.values()),
        "total_file_entries": sum(len(v) for v in file_entries.values()),
    }


def _count_by_status() -> dict[str, int]:
    counts: dict[str, int] = {}
    for run in runs.values():
        counts[run.status] = counts.get(run.status, 0) + 1
    return counts


# ──────────────────────────────────────────────────────────────────────────── #
# AIEStore object wrapper (used by routes / pipeline as singleton)
# ──────────────────────────────────────────────────────────────────────────── #


class AIEStore:
    """Object-oriented interface over the in-memory store."""

    def __init__(self) -> None:
        self._analyses: dict[str, AnalysisRun] = runs
        self._components: dict[str, list[DiscoveredComponent]] = components
        self._relationships: dict[str, list[DiscoveredRelationship]] = relationships
        self._graphs: dict[str, Any] = graphs
        self._descriptors: dict[str, dict] = descriptors
        self._capabilities: dict[str, dict] = capabilities
        self._profiles: dict[str, TechnologyProfile] = tech_profiles
        self._file_inventories: dict[str, list[FileEntry]] = file_entries
        self._logs: dict[str, list[str]] = run_logs

    def store_analysis(self, run: AnalysisRun) -> AnalysisRun:
        if run.run_id in self._analyses:
            return update_run(run.run_id, **run.model_dump(exclude={"run_id"}))
        return create_run(run)

    def get_analysis(self, analysis_id: str) -> AnalysisRun | None:
        return self._analyses.get(analysis_id)

    def list_analyses(self) -> list[AnalysisRun]:
        return list_runs()

    def store_components(self, app_id: str, comps: list[DiscoveredComponent]) -> None:
        self._components[app_id] = list(comps)

    def get_components(self, app_id: str) -> list[DiscoveredComponent]:
        return self._components.get(app_id, [])

    def store_relationships(self, app_id: str, rels: list[DiscoveredRelationship]) -> None:
        self._relationships[app_id] = list(rels)

    def get_relationships(self, app_id: str) -> list[DiscoveredRelationship]:
        return self._relationships.get(app_id, [])

    def store_graph(self, app_id: str, graph: Any) -> None:
        self._graphs[app_id] = graph

    def get_graph(self, app_id: str) -> Any | None:
        return self._graphs.get(app_id)

    def store_descriptor(self, app_id: str, desc: dict) -> None:
        self._descriptors[app_id] = desc

    def get_descriptor(self, app_id: str) -> dict | None:
        return self._descriptors.get(app_id)

    def store_capabilities(self, app_id: str, caps: dict) -> None:
        self._capabilities[app_id] = caps

    def get_capabilities(self, app_id: str) -> dict | None:
        return self._capabilities.get(app_id)

    def store_profile(self, app_id: str, profile: TechnologyProfile) -> None:
        self._profiles[app_id] = profile

    def get_profile(self, app_id: str) -> TechnologyProfile | None:
        return self._profiles.get(app_id)

    def store_file_inventory(self, analysis_id: str, files: list[FileEntry]) -> None:
        self._file_inventories[analysis_id] = list(files)

    def get_file_inventory(self, analysis_id: str) -> list[FileEntry]:
        return self._file_inventories.get(analysis_id, [])

    def store_all(
        self,
        run: AnalysisRun,
        inventory: list[FileEntry] | None = None,
        profile: TechnologyProfile | None = None,
        components: list[DiscoveredComponent] | None = None,
        relationships: list[DiscoveredRelationship] | None = None,
        graph: Any = None,
        descriptor: dict | None = None,
        capabilities: dict | None = None,
    ) -> None:
        run_id = run.run_id
        self.store_analysis(run)
        if inventory is not None:
            self.store_file_inventory(run_id, inventory)
        if profile is not None:
            self.store_profile(run_id, profile)
        if components is not None:
            self.store_components(run_id, components)
        if relationships is not None:
            self.store_relationships(run_id, relationships)
        if graph is not None:
            self.store_graph(run_id, graph)
        if descriptor is not None:
            self.store_descriptor(run_id, descriptor)
        if capabilities is not None:
            self.store_capabilities(run_id, capabilities)


# Global singleton instance
aie_store = AIEStore()

