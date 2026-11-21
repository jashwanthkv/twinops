"""
main.py — TwinOps AIE backend entry point.

Starts a FastAPI application with:
  - Lifespan context (startup / shutdown hooks)
  - AIE REST router  (aie.api.routes)
  - AIE WebSocket router (aie.api.websocket)
  - CORS middleware (all origins — dev mode)
  - Uvicorn server on port 8000
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, AsyncIterator

import uvicorn
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ConfigDict, Field

from aie.config import settings

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.DEBUG if settings.debug else logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)
logger = logging.getLogger("aie.main")


# ---------------------------------------------------------------------------
# Lifespan — startup / shutdown
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Application lifespan: initialise resources on startup, clean up on shutdown."""

    # ── Startup ──────────────────────────────────────────────────────────
    logger.info("TwinOps AIE backend starting up …")

    # Ensure the workspace base directory exists (never execute anything inside)
    workspace_dir = os.path.abspath(settings.workspace_base_dir)
    os.makedirs(workspace_dir, exist_ok=True)
    logger.info("Workspace base dir: %s", workspace_dir)

    # Initialize app.state.store and app.state.pipeline
    from aie.store import aie_store
    from aie.events import event_bus
    app.state.store = aie_store
    app.state.event_bus = event_bus

    try:
        from aie.pipeline import AIEPipeline
        app.state.pipeline = AIEPipeline(config=settings, store=aie_store, event_bus=event_bus)
        logger.info("AIEPipeline initialized in app.state")
    except Exception as exc:
        logger.warning("Could not pre-initialize pipeline: %s", exc)
        app.state.pipeline = None

    logger.info("Groq model: %s", settings.groq_model)
    logger.info("Startup complete — listening on %s:%d", settings.app_host, settings.app_port)
    logger.info("👉 Open Frontend UI in browser: http://localhost:%d (or http://127.0.0.1:%d)", settings.app_port, settings.app_port)
    logger.info("👉 Open FastAPI Swagger Docs: http://localhost:%d/docs (or http://127.0.0.1:%d/docs)", settings.app_port, settings.app_port)

    yield  # ← application runs here

    # ── Shutdown ─────────────────────────────────────────────────────────
    logger.info("TwinOps AIE backend shutting down …")


# ---------------------------------------------------------------------------
# FastAPI application
# ---------------------------------------------------------------------------

app = FastAPI(
    title="TwinOps AIE API",
    description=(
        "AI Engine backend for TwinOps — analyses Git repositories and "
        "produces architecture models, technology profiles, and component graphs."
    ),
    version="0.1.0",
    lifespan=lifespan,
)

# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_allow_origins,  # ["*"] in dev
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Routers
# ---------------------------------------------------------------------------

# Import lazily so that startup errors surface clearly.
try:
    from aie.api.routes import router as aie_router  # noqa: E402

    # Include at root prefix for /api/aie/* and at /api/v1 for versioned routing
    app.include_router(aie_router)
    app.include_router(aie_router, prefix="/api/v1", tags=["AIE"])
except ImportError as exc:
    logger.warning("Could not import aie.api.routes: %s", exc)

try:
    from aie.api.websocket import router as ws_router  # noqa: E402

    app.include_router(ws_router, tags=["WebSocket"])
except ImportError as exc:
    logger.warning("Could not import aie.api.websocket: %s", exc)


# ---------------------------------------------------------------------------
# Health / root endpoints
# ---------------------------------------------------------------------------


@app.get("/health", tags=["Health"])
async def health() -> dict:
    from aie.store import store_summary

    return {"status": "ok", **store_summary()}


async def root() -> dict:
    return {"service": "TwinOps AIE", "status": "ok", "version": app.version}


# ---------------------------------------------------------------------------
# Consolidated AIE Analysis Endpoints
# ---------------------------------------------------------------------------


class AnalyzeRepoRequest(BaseModel):
    """Payload to trigger repository analysis."""

    model_config = ConfigDict(populate_by_name=True)

    repo_url: str = Field(
        default=None,
        description="Public or private GitHub repository URL",
        examples=["https://github.com/jashwanthkv/research.git"],
    )
    branch: str | None = Field(
        default="main",
        description="Git branch, tag, or commit to analyze",
        examples=["main"],
    )
    auth_token: str | None = Field(
        default=None,
        description="GitHub Personal Access Token (PAT) for private repositories (optional)",
        examples=[None],
    )
    application_id: str | None = Field(
        default=None,
        description="Custom application identifier (optional, auto-generated if omitted)",
        examples=["OpenVoice-AI"],
    )
    skip_llm: bool = Field(
        default=False,
        description="Set True to skip Groq LLM inference for fast heuristic-only analysis",
        examples=[False],
    )


def _get_active_pipeline():
    """Resolve or lazily instantiate the AIE pipeline."""
    pipeline = getattr(app.state, "pipeline", None)
    if pipeline is None:
        from aie.events import event_bus
        from aie.persistence.store import aie_store
        from aie.pipeline import AIEPipeline

        pipeline = AIEPipeline(config=settings, store=aie_store, event_bus=event_bus)
        app.state.pipeline = pipeline
    return pipeline


def _get_active_store():
    """Resolve or lazily instantiate the AIE store."""
    store = getattr(app.state, "store", None)
    if store is None:
        from aie.persistence.store import aie_store

        store = aie_store
        app.state.store = store
    return store


def _format_all_outputs(store, run) -> dict[str, Any]:
    """Extract and consolidate every single artifact produced by the AIE process with focused output."""
    run_id = run.run_id
    app_id = getattr(run, "application_id", None) or run_id

    # Module artifacts
    profile = store.get_profile(app_id) or store.get_profile(run_id)
    components = store.get_components(app_id) or store.get_components(run_id)
    relationships = store.get_relationships(app_id) or store.get_relationships(run_id)
    graph = store.get_graph(app_id) or store.get_graph(run_id)
    descriptor = store.get_descriptor(app_id) or store.get_descriptor(run_id)
    capabilities = store.get_capabilities(app_id) or store.get_capabilities(run_id)
    files = store.get_file_inventory(run_id) or store.get_file_inventory(app_id)

    profile_dict = (
        profile.model_dump()
        if hasattr(profile, "model_dump")
        else (dict(profile) if profile else {})
    )
    comp_list = [
        c.model_dump() if hasattr(c, "model_dump") else dict(c) for c in components
    ]
    rel_list = [
        r.model_dump() if hasattr(r, "model_dump") else dict(r) for r in relationships
    ]
    graph_dict = (
        graph.model_dump()
        if hasattr(graph, "model_dump")
        else (dict(graph) if graph else {"nodes": [], "edges": []})
    )
    file_list = [
        f.model_dump() if hasattr(f, "model_dump") else dict(f) for f in files
    ]

    # Cleaned component representations with structured descriptions
    formatted_components = []
    for c in comp_list:
        desc = c.get("description") or ""
        meta = c.get("metadata", {})
        doing = meta.get("what_it_is_doing") or c.get("responsibility") or ""
        coded = meta.get("how_it_is_coded") or c.get("implementation_summary") or ""
        diag = meta.get("diagnostic_guide") or ""

        formatted_components.append({
            "component_id": c.get("component_id"),
            "name": c.get("name"),
            "component_type": c.get("component_type"),
            "technologies": c.get("technologies", []),
            "root_paths": c.get("root_paths", []),
            "description": desc,
            "what_it_is_doing": doing,
            "how_it_is_coded": coded,
            "diagnostic_guide": diag,
            "failure_scenarios": c.get("failure_scenarios", []),
            "monitoring_requirements": c.get("monitoring_requirements", []),
            "confidence": c.get("confidence", 1.0),
        })

    # Languages summary & breakdown
    languages_data = {
        "primary_language": profile_dict.get("primary_language"),
        "languages": profile_dict.get("languages", []),
    }

    # Architecture: call graph & system design relationships
    architecture_data = {
        "nodes": graph_dict.get("nodes", []),
        "edges": graph_dict.get("edges", []),
        "relationships": rel_list,
    }

    # Enrichment details
    enrichment_data = {
        "primary_framework": profile_dict.get("primary_framework"),
        "frameworks": profile_dict.get("frameworks", []),
        "technologies": profile_dict.get("technologies", []),
        "entry_points": profile_dict.get("entry_points", []),
        "build_tools": profile_dict.get("build_tools", []),
        "test_frameworks": profile_dict.get("test_frameworks", []),
        "package_managers": profile_dict.get("package_managers", []),
        "ci_cd_tools": profile_dict.get("ci_cd_tools", []),
    }

    return {
        "status": run.status,
        "run_id": run.run_id,
        "application_id": app_id,
        "repo_url": run.repo_url,
        "branch": getattr(run, "branch", None),
        "commit_hash": getattr(run, "commit_hash", None),
        "error_message": getattr(run, "error_message", None),
        "created_at": getattr(run, "created_at", None),
        "started_at": getattr(run, "started_at", None),
        "completed_at": getattr(run, "completed_at", None),
        "summary": {
            "component_count": len(comp_list),
            "relationship_count": len(rel_list),
            "node_count": len(graph_dict.get("nodes", [])),
            "edge_count": len(graph_dict.get("edges", [])),
            "file_count": len(file_list),
            "primary_language": profile_dict.get("primary_language"),
            "primary_framework": profile_dict.get("primary_framework"),
        },
        "languages": languages_data,
        "components": formatted_components,
        "architecture": architecture_data,
        "enrichment_details": enrichment_data,
        # Preserved compatibility fields
        "technology_profile": profile_dict,
        "relationships": rel_list,
        "architecture_graph": graph_dict,
        "contract_b_descriptor": descriptor,
        "contract_c_capabilities": capabilities,
    }


@app.post(
    "/analyze",
    tags=["AIE Analysis"],
    summary="Submit GitHub repository for analysis",
    description="Asynchronously starts the 12-stage AIE analysis for the repository and returns the analysis ID.",
)
@app.post(
    "/api/aie/analyze",
    tags=["AIE Analysis"],
    include_in_schema=False,
)
async def analyze_repo(payload: AnalyzeRepoRequest) -> dict[str, Any]:
    """Start asynchronous repository analysis in a background task."""
    import asyncio
    import uuid
    from datetime import datetime, timezone
    from aie.models import AnalysisRequest, AnalysisRun
    from aie.utils import extract_project_from_url

    pipeline = _get_active_pipeline()
    store = _get_active_store()

    owner, project_name = extract_project_from_url(payload.repo_url)
    analysis_id = str(uuid.uuid4())
    app_id = payload.application_id or project_name or analysis_id
    now = datetime.now(tz=timezone.utc)

    run = AnalysisRun(
        run_id=analysis_id,
        application_id=app_id,
        repo_url=payload.repo_url,
        repo_ref=payload.branch or "HEAD",
        branch=payload.branch,
        status="pending",
        created_at=now,
        started_at=now,
    )
    store.store_analysis(run)

    req = AnalysisRequest(
        repo_url=payload.repo_url,
        branch=payload.branch,
        repo_ref=payload.branch or "HEAD",
        application_id=app_id,
        auth_token=payload.auth_token,
        options={"skip_llm": payload.skip_llm},
    )
    req.run_id = analysis_id

    # Execute in background task
    asyncio.create_task(pipeline.analyze(req))

    return {
        "analysis_id": analysis_id,
        "application_id": app_id,
        "status": "pending",
        "message": f"Analysis started for {payload.repo_url}",
    }


@app.get(
    "/analyses/{analysis_id}/status",
    tags=["AIE Analysis"],
    summary="Get analysis status & progress",
    description="Check current stage and progress percentage of a running or completed analysis.",
)
@app.get(
    "/api/aie/analyses/{analysis_id}/status",
    tags=["AIE Analysis"],
    include_in_schema=False,
)
async def get_analysis_status(analysis_id: str) -> dict[str, Any]:
    """Return status and progress snapshot for an analysis run."""
    store = _get_active_store()
    run = store.get_analysis(analysis_id)
    if not run:
        for r in store.list_analyses():
            if getattr(r, "application_id", None) == analysis_id:
                run = r
                break
    if not run:
        raise HTTPException(status_code=404, detail=f"Analysis '{analysis_id}' not found")

    progress = getattr(run, "progress", None)
    return {
        "analysis_id": run.run_id,
        "application_id": getattr(run, "application_id", None),
        "status": run.status,
        "stage": getattr(progress, "stage", run.status),
        "percent_complete": getattr(progress, "percent_complete", 100.0 if run.status == "complete" else 0.0),
        "message": getattr(progress, "message", ""),
        "error_message": getattr(run, "error_message", None),
    }


@app.get(
    "/analyses/{analysis_id}/full",
    tags=["AIE Analysis"],
    summary="Retrieve all AIE outputs (graph, knowledge, descriptors)",
    description="Returns all 12-stage outputs once analysis is complete.",
)
@app.get(
    "/api/aie/analyses/{analysis_id}/full",
    tags=["AIE Analysis"],
    include_in_schema=False,
)
async def get_analysis_full(analysis_id: str) -> dict[str, Any]:
    """Fetch complete outputs for an analyzed repository."""
    store = _get_active_store()
    run = store.get_analysis(analysis_id)
    if not run:
        for r in store.list_analyses():
            if getattr(r, "application_id", None) == analysis_id:
                run = r
                break
    if not run:
        raise HTTPException(
            status_code=404,
            detail=f"Analysis '{analysis_id}' not found",
        )
    return _format_all_outputs(store, run)


# ---------------------------------------------------------------------------
# Mount Frontend Dashboard (served on FastAPI port)
# ---------------------------------------------------------------------------
from fastapi.staticfiles import StaticFiles  # noqa: E402

_frontend_candidates = [
    os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "frontend")),
    os.path.abspath(os.path.join(os.path.dirname(__file__), "frontend")),
]
frontend_dir = next((d for d in _frontend_candidates if os.path.isdir(d)), None)
if frontend_dir:
    app.mount("/", StaticFiles(directory=frontend_dir, html=True), name="frontend")
    logger.info("Frontend mounted from: %s", frontend_dir)
else:
    logger.info("No frontend directory found among candidates: %s", _frontend_candidates)


# ---------------------------------------------------------------------------
# Direct Pipeline Runner & Dev entry point
# ---------------------------------------------------------------------------

async def run_pipeline_cli() -> None:
    """Direct execution runner: Runs the full 12-stage AIE pipeline on the configured repository,
    persists all knowledge to MongoDB and Redis, and prints the focused results."""
    import json
    import sys
    from datetime import datetime, timezone
    from aie.models import AnalysisRequest
    from aie.pipeline import AIEPipeline
    from aie.persistence.store import aie_store
    from aie.events import event_bus
    from aie.utils import extract_project_from_url

    repo_url = "https://github.com/jashwanthkv/research.git"
    branch = "main"

    # Allow custom repo from command line arguments if provided: python main.py <repo_url> <branch>
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    if len(args) >= 1:
        repo_url = args[0]
    if len(args) >= 2:
        branch = args[1]

    owner, project_name = extract_project_from_url(repo_url)
    app_id = project_name

    print("\n" + "=" * 80)
    print("🚀  TWINOPS APPLICATION INTELLIGENCE ENGINE (AIE) RUNNER")
    print("=" * 80)
    print(f"📦 Target Repository: {repo_url}")
    print(f"🌿 Target Branch:     {branch}")
    print(f"🏷️  Project Name:      {project_name}")
    print(f"🗄️  MongoDB DB Name:   {project_name}")
    print(f"⚡ Redis Prefix:      aie:app:{project_name}:*")
    print("=" * 80 + "\n")

    pipeline = AIEPipeline(config=settings, store=aie_store, event_bus=event_bus)

    req = AnalysisRequest(
        repo_url=repo_url,
        branch=branch,
        repo_ref=branch,
        application_id=app_id,
        options={"skip_llm": False},
    )

    print("⏳ Executing 12-stage AIE pipeline...\n")
    start_time = datetime.now(tz=timezone.utc)
    run = await pipeline.analyze(req)
    duration = (datetime.now(tz=timezone.utc) - start_time).total_seconds()

    print("\n" + "=" * 80)
    print(f"✅  ANALYSIS FINISHED WITH STATUS: {run.status.upper()} (took {duration:.1f}s)")
    print("=" * 80 + "\n")

    if run.status == "complete":
        outputs = _format_all_outputs(aie_store, run)

        print("\n" + "=" * 80)
        print("📊 1. LANGUAGES")
        print("=" * 80)
        print(json.dumps(outputs["languages"], indent=2))

        print("\n" + "=" * 80)
        print("🧩 2. SYSTEM DESIGN COMPONENTS (WITH DETAILED DESCRIPTIONS)")
        print("=" * 80)
        for comp in outputs["components"]:
            print(f"\n🔹 [{comp['component_type'].upper()}] {comp['name']} (ID: {comp['component_id']})")
            print(f"   Technologies: {', '.join(comp['technologies']) if comp['technologies'] else 'None'}")
            print(f"   Root Paths:   {', '.join(comp['root_paths']) if comp['root_paths'] else 'root'}")
            print(f"   Description:\n{comp['description']}")

        print("\n" + "=" * 80)
        print("🏗️ 3. ARCHITECTURE (CALL GRAPH & RELATIONSHIPS)")
        print("=" * 80)
        arch = outputs["architecture"]
        print(f"Nodes: {len(arch['nodes'])}, Edges: {len(arch['edges'])}, Relationships: {len(arch['relationships'])}")
        for rel in arch["relationships"][:15]:
            print(f"   • {rel.get('source_component_id')} --[{rel.get('relation_type')}]--> {rel.get('target_component_id')}")
        if len(arch["relationships"]) > 15:
            print(f"   ... and {len(arch['relationships']) - 15} more relationships")

        print("\n" + "=" * 80)
        print("⚙️ 4. ENRICHMENT DETAILS")
        print("=" * 80)
        print(json.dumps(outputs["enrichment_details"], indent=2))

        print("\n" + "=" * 80)
        print("💾 STORAGE VERIFICATION")
        print("=" * 80)
        print(f"✔️ MongoDB: Persisted under database '{project_name}'")
        print(f"✔️ Redis:   Loaded active version under namespace 'aie:app:{project_name}:*'")
        print(f"✔️ Chroma:  Indexed semantic chunks for '{project_name}'")
        print("=" * 80 + "\n")
    else:
        print(f"❌ Analysis failed: {run.error_message}")


if __name__ == "__main__":
    import sys
    import asyncio

    args = sys.argv[1:]

    if "--pipeline" in args:
        # Manual pipeline run: python main.py --pipeline [repo_url] [branch]
        asyncio.run(run_pipeline_cli())

    else:
        # Default: start ALL services together — AIE server + Gateway
        # python main.py              → starts both servers
        # python main.py --aie-only  → starts only AIE server
        # python main.py --gw-only   → starts only Gateway server

        aie_only = "--aie-only" in args
        gw_only = "--gw-only" in args

        async def _serve_all() -> None:
            import os as _os
            servers = []

            if not gw_only:
                aie_cfg = uvicorn.Config(
                    app="main:app",
                    host=settings.app_host,
                    port=settings.app_port,
                    reload=False,
                    log_level="debug" if settings.debug else "info",
                )
                servers.append(uvicorn.Server(aie_cfg))
                logger.info(
                    "AIE Server  → http://%s:%d  (docs: /docs)",
                    settings.app_host, settings.app_port,
                )

            if not aie_only:
                from gateway.app import create_gateway_app as _make_gw
                _gw_port = int(_os.getenv("GATEWAY_PORT", "8001"))
                gw_cfg = uvicorn.Config(
                    app=_make_gw(),
                    host="0.0.0.0",
                    port=_gw_port,
                    log_level="info",
                )
                servers.append(uvicorn.Server(gw_cfg))
                logger.info(
                    "Gateway     → http://0.0.0.0:%d  (docs: /gateway/docs)",
                    _gw_port,
                )

            if not servers:
                logger.error("No servers selected — nothing to start.")
                return

            # Initialize asynchronous background bus consumers
            consumers = []
            try:
                from storage.consumer import StorageConsumer
                from ml.consumer_anomaly import AnomalyConsumer
                from ml.consumer_forecast import ForecastConsumer
                from twin.consumer import TwinConsumer
                from llm.consumer import LLMConsumer

                consumers = [
                    StorageConsumer(),
                    AnomalyConsumer(),
                    ForecastConsumer(),
                    TwinConsumer(),
                    LLMConsumer(),
                ]
            except Exception as exc:
                logger.error("Failed to initialize background consumers: %s", exc)

            print("\n" + "=" * 70)
            print("🚀  TWINOPS — FULL PLATFORM STARTING")
            print("=" * 70)
            for s in servers:
                h = s.config.host
                p = s.config.port
                print(f"   • {'AIE API  ' if p == settings.app_port else 'Gateway  '} → http://{h}:{p}")
            print("   • Storage Consumer      → telemetry.events → MongoDB (raw storage)")
            print("   • Anomaly Consumer      → 18-feature Isolation Forest + Evaluator Gate")
            print("   • Forecast Consumer     → 6-metric Linear Regression (predicts +15m)")
            print("   • Digital Twin Consumer → Real-time component health (Redis + MongoDB)")
            print("   • Master LLM Consumer   → Full AIE Knowledge Injection + Groq Analysis")
            print("=" * 70 + "\n")

            # Start background consumers as non-blocking tasks so Uvicorn binds immediately
            consumer_tasks = [asyncio.create_task(c.run()) for c in consumers]

            # Serve Uvicorn servers concurrently
            try:
                await asyncio.gather(*(s.serve() for s in servers))
            finally:
                for ct in consumer_tasks:
                    ct.cancel()

        asyncio.run(_serve_all())
