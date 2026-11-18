"""
aie/api/routes.py — AIE REST API Layer.

Endpoints for triggering repository analyses, querying status, and retrieving
discovered architecture components, relationships, graphs, descriptors, and capability catalogs.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from aie.config import settings
from aie.graph.generator import ArchitectureGraph
from aie.models import (
    AnalysisRequest,
    AnalysisRun,
    DiscoveredComponent,
    DiscoveredRelationship,
    FileEntry,
    TechnologyProfile,
)
from aie.persistence.store import AIEStore, aie_store
from aie.pipeline import AIEPipeline

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/aie", tags=["aie"])

_pipeline_instance: AIEPipeline | None = None


def get_store(request: Request) -> AIEStore:
    """Dependency provider for AIEStore."""
    store = getattr(request.app.state, "store", None)
    if store is not None:
        return store
    return aie_store


def get_pipeline(request: Request) -> AIEPipeline:
    """Dependency provider for AIEPipeline."""
    global _pipeline_instance
    pipeline = getattr(request.app.state, "pipeline", None)
    if pipeline is not None:
        return pipeline
    if _pipeline_instance is None:
        _pipeline_instance = AIEPipeline(config=settings, store=aie_store)
    return _pipeline_instance


# ──────────────────────────────────────────────────────────────────────────── #
# Analysis Operations
# ──────────────────────────────────────────────────────────────────────────── #


@router.post("/analyze", status_code=202)
async def start_analysis(
    body: AnalysisRequest,
    store: AIEStore = Depends(get_store),
    pipeline: AIEPipeline = Depends(get_pipeline),
) -> dict[str, Any]:
    """Start asynchronous repository analysis in a background task."""
    analysis_id = str(uuid.uuid4())
    app_id = getattr(body, "application_id", None) or analysis_id
    now = datetime.now(tz=timezone.utc)

    # Initial pending record
    run = AnalysisRun(
        run_id=analysis_id,
        application_id=app_id,
        repo_url=body.repo_url,
        repo_ref=getattr(body, "branch", None) or getattr(body, "repo_ref", "HEAD"),
        branch=getattr(body, "branch", None),
        status="pending",
        created_at=now,
        started_at=now,
    )
    store.store_analysis(run)

    # Pass analysis_id and app_id along in request
    body.application_id = app_id
    if hasattr(body, "run_id"):
        body.run_id = analysis_id
    if hasattr(body, "analysis_id"):
        body.analysis_id = analysis_id

    # Launch background task
    asyncio.create_task(pipeline.analyze(body))

    logger.info("Started analysis task %s for repo %s", analysis_id, body.repo_url)
    return {
        "analysis_id": analysis_id,
        "status": "pending",
        "application_id": app_id,
    }


@router.get("/analyses")
async def list_analyses(
    store: AIEStore = Depends(get_store),
) -> list[dict[str, Any]]:
    """List all analysis runs ordered newest first."""
    runs = store.list_analyses()
    return [r.model_dump() if hasattr(r, "model_dump") else dict(r) for r in runs]


@router.get("/analyses/{analysis_id}")
async def get_analysis(
    analysis_id: str,
    store: AIEStore = Depends(get_store),
) -> dict[str, Any]:
    """Get lifecycle status and summary of a specific analysis run."""
    run = store.get_analysis(analysis_id)
    if not run:
        raise HTTPException(status_code=404, detail=f"Analysis '{analysis_id}' not found")
    return run.model_dump() if hasattr(run, "model_dump") else dict(run)


@router.delete("/analyses/{analysis_id}")
async def cancel_analysis(
    analysis_id: str,
    store: AIEStore = Depends(get_store),
) -> dict[str, Any]:
    """Cancel a running analysis or mark as cancelled."""
    run = store.get_analysis(analysis_id)
    if not run:
        raise HTTPException(status_code=404, detail=f"Analysis '{analysis_id}' not found")

    if run.status in ("complete", "completed", "failed", "cancelled"):
        return {
            "analysis_id": analysis_id,
            "status": run.status,
            "message": f"Analysis already {run.status}",
        }

    run.status = "cancelled"
    run.error_message = "Analysis cancelled by user"
    run.completed_at = datetime.now(tz=timezone.utc)
    store.store_analysis(run)

    logger.info("Cancelled analysis run %s", analysis_id)
    return {
        "analysis_id": analysis_id,
        "status": "cancelled",
        "message": "Analysis run marked as cancelled",
    }


# ──────────────────────────────────────────────────────────────────────────── #
# Application Architecture Artifacts
# ──────────────────────────────────────────────────────────────────────────── #


@router.get("/apps/{app_id}/profile")
async def get_profile(
    app_id: str,
    store: AIEStore = Depends(get_store),
) -> dict[str, Any]:
    """Retrieve discovered TechnologyProfile for an application."""
    profile = store.get_profile(app_id)
    if not profile:
        raise HTTPException(status_code=404, detail=f"No technology profile found for app '{app_id}'")
    return profile.model_dump() if hasattr(profile, "model_dump") else dict(profile)


@router.get("/apps/{app_id}/components")
async def get_components(
    app_id: str,
    store: AIEStore = Depends(get_store),
) -> list[dict[str, Any]]:
    """Retrieve list of discovered components for an application."""
    components = store.get_components(app_id)
    return [c.model_dump() if hasattr(c, "model_dump") else dict(c) for c in components]


@router.get("/apps/{app_id}/components/{component_id}")
async def get_component(
    app_id: str,
    component_id: str,
    store: AIEStore = Depends(get_store),
) -> dict[str, Any]:
    """Retrieve detail of a specific discovered component."""
    component = store.get_component(app_id, component_id)
    if not component:
        raise HTTPException(
            status_code=404,
            detail=f"Component '{component_id}' not found in app '{app_id}'",
        )
    return component.model_dump() if hasattr(component, "model_dump") else dict(component)


@router.get("/apps/{app_id}/relationships")
async def get_relationships(
    app_id: str,
    store: AIEStore = Depends(get_store),
) -> list[dict[str, Any]]:
    """Retrieve list of discovered inter-component relationships."""
    relationships = store.get_relationships(app_id)
    return [r.model_dump() if hasattr(r, "model_dump") else dict(r) for r in relationships]


@router.get("/apps/{app_id}/graph")
async def get_graph(
    app_id: str,
    store: AIEStore = Depends(get_store),
) -> dict[str, Any]:
    """Retrieve complete ArchitectureGraph for an application."""
    graph = store.get_graph(app_id)
    if not graph:
        raise HTTPException(status_code=404, detail=f"No architecture graph found for app '{app_id}'")
    return graph.model_dump() if hasattr(graph, "model_dump") else dict(graph)


@router.get("/apps/{app_id}/descriptor")
async def get_descriptor(
    app_id: str,
    store: AIEStore = Depends(get_store),
) -> dict[str, Any]:
    """Retrieve auto-generated Application Descriptor (Contract B)."""
    descriptor = store.get_descriptor(app_id)
    if not descriptor:
        raise HTTPException(
            status_code=404, detail=f"No Contract B descriptor found for app '{app_id}'"
        )
    return descriptor


@router.get("/apps/{app_id}/capabilities")
async def get_capabilities(
    app_id: str,
    store: AIEStore = Depends(get_store),
) -> dict[str, Any]:
    """Retrieve inferred Capability Catalog (Contract C)."""
    capabilities = store.get_capabilities(app_id)
    if not capabilities:
        raise HTTPException(
            status_code=404, detail=f"No Contract C capabilities found for app '{app_id}'"
        )
    return capabilities


@router.get("/apps/{app_id}/files")
async def get_files(
    app_id: str,
    store: AIEStore = Depends(get_store),
) -> list[dict[str, Any]]:
    """Retrieve repository file inventory for an application or analysis ID."""
    files = store.get_file_inventory(app_id)
    if not files:
        # Search by associated run ID
        runs = [
            r
            for r in store.list_analyses()
            if getattr(r, "application_id", None) == app_id or r.run_id == app_id
        ]
        if runs:
            files = store.get_file_inventory(runs[0].run_id)

    if not files:
        raise HTTPException(status_code=404, detail=f"No file inventory found for app '{app_id}'")
    return [f.model_dump() if hasattr(f, "model_dump") else dict(f) for f in files]


# ──────────────────────────────────────────────────────────────────────────── #
# Knowledge Retrieval Layer for TwinOps & SLM Reasoning
# ──────────────────────────────────────────────────────────────────────────── #


@router.get("/apps/{app_id}/components/{component_id}/neighbours")
async def get_component_neighbours(
    app_id: str,
    component_id: str,
    direction: str = "all",  # "all", "out", "in"
) -> dict[str, Any]:
    """Retrieve incoming and outgoing neighbours from Redis adjacency sets."""
    from aie.persistence.services import retrieval_service

    return await retrieval_service.get_component_neighbours(
        app_id=app_id,
        comp_id=component_id,
        direction=direction,
    )


@router.get("/apps/{app_id}/components/{component_id}/traverse")
async def traverse_component_relationships(
    app_id: str,
    component_id: str,
    direction: str = "out",
    max_depth: int = 3,
) -> list[dict[str, Any]]:
    """Traverse multi-hop dependency paths starting from a component using Redis graph."""
    from aie.persistence.services import retrieval_service

    return await retrieval_service.traverse_relationships(
        app_id=app_id,
        start_comp_id=component_id,
        direction=direction,
        max_depth=max_depth,
    )


@router.get("/apps/{app_id}/active-version")
async def get_active_version(app_id: str) -> dict[str, Any]:
    """Get the active analysis run version for an application."""
    from aie.persistence.services import retrieval_service

    app_doc = await retrieval_service.app_repo.get(app_id)
    if not app_doc:
        raise HTTPException(status_code=404, detail=f"Application '{app_id}' not found")
    return app_doc


@router.post("/apps/{app_id}/search")
async def semantic_search_knowledge(
    app_id: str,
    query: str,
    component_id: str | None = None,
    content_type: str | None = None,
    n_results: int = 5,
) -> list[dict[str, Any]]:
    """Perform semantic search through ChromaDB scoped to this application's active knowledge."""
    from aie.persistence.services import retrieval_service

    return await retrieval_service.semantic_search(
        app_id=app_id,
        query=query,
        component_id=component_id,
        content_type=content_type,
        n_results=n_results,
    )


@router.post("/apps/{app_id}/rebuild-redis")
async def rebuild_redis_cache(app_id: str) -> dict[str, Any]:
    """Rebuild in-memory Redis knowledge and graph adjacency lists from MongoDB."""
    from aie.persistence.services import retrieval_service

    success = await retrieval_service.rebuild_redis_from_mongodb(app_id)
    if not success:
        raise HTTPException(
            status_code=400,
            detail=f"Could not rebuild Redis cache for application '{app_id}'",
        )
    return {"status": "ok", "message": f"Redis cache rebuilt from MongoDB for app '{app_id}'"}

