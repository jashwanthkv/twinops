"""
aie/pipeline.py — End-to-End AIE Pipeline Orchestrator.

Coordinates all 12 modules of the Application Intelligence Engine:
Repo Ingestion → File Classifier → Tech Discovery → Docs/Config Parsing →
Static AST Analysis → Component Identification → Relationship Analysis →
LLM Knowledge Extraction → Technical Enrichment → Evidence Validation →
Architecture Graph Generation → Knowledge Persistence & Vector Indexing.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from aie.classifier.classifier import FileClassifier
from aie.classifier.filter import FileFilter
from aie.classifier.inventory import InventoryBuilder
from aie.classifier.language_detector import LanguageDetector
from aie.components.identifier import ComponentIdentifier
from aie.config import AIEConfig, settings
from aie.discovery.engine import DiscoveryEngine
from aie.docs_analyzer.config_extractor import ConfigExtractor
from aie.docs_analyzer.doc_parser import DocParser
from aie.enrichment.enricher import TechnicalEnricher
from aie.events import AnalysisEventBus, event_bus as global_event_bus
from aie.graph.descriptor_gen import DescriptorGenerator
from aie.graph.generator import ArchitectureGraph, ArchitectureGraphGenerator
from aie.graph.twin_format import TwinFormatConverter
from aie.ingestion.manager import AnalysisWorkspace, IngestionManager
from aie.knowledge.context_builder import ContextBuilder
from aie.knowledge.extractor import KnowledgeExtractor
from aie.models import (
    AnalysisProgress,
    AnalysisRequest,
    AnalysisRun,
    ComponentType,
    DiscoveredComponent,
    DiscoveredRelationship,
    FileEntry,
    TechnologyProfile,
)
from aie.persistence.store import AIEStore, aie_store
from aie.persistence.vector_store import VectorStore
from aie.relationships.analyzer import RelationshipAnalyzer
from aie.static_analysis.engine import StaticAnalysisEngine
from aie.validation.evidence import EvidenceManager

logger = logging.getLogger(__name__)


class AIEPipeline:
    """Orchestrates repository ingestion, AST analysis, component identification,
    knowledge extraction, graph generation, and persistence.
    """

    def __init__(
        self,
        config: AIEConfig | None = None,
        store: AIEStore | None = None,
        event_bus: AnalysisEventBus | None = None,
    ) -> None:
        self.config = config or settings
        self.store = store or aie_store
        self.event_bus = event_bus or global_event_bus

        # Module 1: Ingestion
        workspace_dir = Path(getattr(self.config, "workspace_base_dir", "./aie_workspaces")).resolve()
        self.ingestion = IngestionManager(workspace_base_dir=workspace_dir)

        # Module 2: Classifier
        self.file_filter = FileFilter()
        self.file_classifier = FileClassifier()
        self.language_detector = LanguageDetector()
        self.inventory_builder = InventoryBuilder()

        # Module 3: Discovery
        self.discovery = DiscoveryEngine()

        # Module 4: Docs & Config
        self.doc_parser = DocParser()
        self.config_extractor = ConfigExtractor()

        # Module 5: Static AST Analysis
        self.static_analysis = StaticAnalysisEngine()

        # Module 6: Component Identification
        self.component_identifier = ComponentIdentifier()

        # Module 7: Relationship Analysis
        self.relationship_analyzer = RelationshipAnalyzer()

        # Module 8: Knowledge Extraction
        self.context_builder = ContextBuilder()
        self.knowledge_extractor = KnowledgeExtractor(config=self.config)

        # Module 9: Technical Enrichment
        self.enricher = TechnicalEnricher()

        # Module 10: Evidence Validation
        self.evidence_manager = EvidenceManager()

        # Module 11: Graph & Descriptor Generation
        self.graph_generator = ArchitectureGraphGenerator()
        self.descriptor_generator = DescriptorGenerator()
        self.twin_converter = TwinFormatConverter()

        # Module 12: Ephemeral Vector Store
        self.vector_store = VectorStore()

        logger.info("AIEPipeline initialized with all 12 modules")

    async def _update_status(
        self,
        run: AnalysisRun,
        step: str,
        progress: float,
        message: str,
        stage_index: int = 0,
        total_stages: int = 12,
        files_processed: int = 0,
        files_total: int = 0,
    ) -> None:
        """Update run progress and emit progress event to event bus."""
        run.progress = AnalysisProgress(
            stage=step,
            stage_index=stage_index,
            total_stages=total_stages,
            files_processed=files_processed,
            files_total=files_total,
            message=message,
            percent_complete=float(progress),
        )
        self.store.store_analysis(run)

        event_payload = {
            "type": "progress",
            "step": step,
            "stage": step,
            "progress": int(progress),
            "percent_complete": float(progress),
            "message": message,
            "status": run.status,
            "run_id": run.run_id,
            "analysis_id": run.run_id,
        }
        await self.event_bus.publish(run.run_id, event_payload)
        logger.info("[%s] %s (%s%%): %s", run.run_id, step, progress, message)
        stage_str = f"Stage {stage_index}/{total_stages}" if stage_index > 0 else step
        print(f"  [AIE] 🚀 {stage_str} ({int(progress)}%): {message}", flush=True)

    async def _update(self, run: AnalysisRun, step: str, progress: int, message: str = "") -> None:
        """Compatibility alias for _update_status."""
        await self._update_status(run, step, float(progress), message)

    async def analyze(self, request: AnalysisRequest) -> AnalysisRun:
        """Execute end-to-end repository analysis pipeline."""
        from aie.utils import extract_project_from_url

        owner, project_name = extract_project_from_url(request.repo_url)
        run_id = getattr(request, "run_id", None) or str(uuid.uuid4())
        app_id = getattr(request, "application_id", None) or project_name or run_id
        branch = getattr(request, "branch", None) or getattr(request, "repo_ref", "HEAD")
        now = datetime.now(tz=timezone.utc)

        print(f"\n[AIE] 📥 Starting Analysis for: {request.repo_url} (App: '{app_id}', Branch: '{branch}', Run ID: {run_id[:8]}...)", flush=True)

        run = AnalysisRun(
            run_id=run_id,
            application_id=app_id,
            repo_url=request.repo_url,
            repo_ref=branch,
            branch=branch,
            status="pending",
            created_at=now,
            started_at=now,
        )
        self.store.store_analysis(run)

        workspace: AnalysisWorkspace | None = None
        auth_token = getattr(request, "auth_token", None) or getattr(self.config, "github_token", None) or None
        options = getattr(request, "options", None) or {}
        skip_llm = options.get("skip_llm", False)

        try:
            # ──────────────────────────────────────────────────────────────
            # Step 1: Clone Repository (Module 1)
            # ──────────────────────────────────────────────────────────────
            await self._update_status(
                run,
                step="cloning",
                progress=5.0,
                message=f"Cloning repository {request.repo_url}...",
                stage_index=1,
            )
            run.status = "cloning"
            self.store.store_analysis(run)

            clone_func = self.ingestion.clone
            target_branch = branch if branch != "HEAD" else None
            if inspect.iscoroutinefunction(clone_func):
                workspace = await clone_func(
                    repo_url=request.repo_url,
                    branch=target_branch,
                    auth_token=auth_token,
                    workspace_id=run_id,
                )
            else:
                workspace = await asyncio.to_thread(
                    clone_func,
                    repo_url=request.repo_url,
                    branch=target_branch,
                    auth_token=auth_token,
                    workspace_id=run_id,
                )

            if workspace and hasattr(workspace, "commit_hash"):
                run.commit_hash = workspace.commit_hash
            logger.info("Cloned repo to workspace: %s", getattr(workspace, "root_path", "unknown"))

            # ──────────────────────────────────────────────────────────────
            # Step 2: Classify Files (Module 2)
            # ──────────────────────────────────────────────────────────────
            await self._update_status(
                run,
                step="filtering",
                progress=15.0,
                message="Scanning and classifying repository files...",
                stage_index=2,
            )
            run.status = "analysing"

            build_inventory_func = self.inventory_builder.build
            if inspect.iscoroutinefunction(build_inventory_func):
                inventory: list[FileEntry] = await build_inventory_func(
                    workspace=workspace,
                    file_filter=self.file_filter,
                    classifier=self.file_classifier,
                    language_detector=self.language_detector,
                )
            else:
                inventory = await asyncio.to_thread(
                    build_inventory_func,
                    workspace=workspace,
                    file_filter=self.file_filter,
                    classifier=self.file_classifier,
                    language_detector=self.language_detector,
                )

            run.file_count = len(inventory)
            self.store.store_file_inventory(run_id, inventory)
            logger.info("Discovered %d file entries in inventory", len(inventory))

            # ──────────────────────────────────────────────────────────────
            # Step 3: Discover Tech Stack (Module 3)
            # ──────────────────────────────────────────────────────────────
            await self._update_status(
                run,
                step="discovery",
                progress=25.0,
                message="Discovering technology stack, languages, and frameworks...",
                stage_index=3,
                files_processed=len(inventory),
                files_total=len(inventory),
            )

            discover_func = self.discovery.discover
            if inspect.iscoroutinefunction(discover_func):
                profile: TechnologyProfile = await discover_func(workspace, inventory)
            else:
                profile = await asyncio.to_thread(discover_func, workspace, inventory)

            profile.run_id = run_id
            self.store.store_profile(run_id, profile)
            if app_id != run_id:
                self.store.store_profile(app_id, profile)

            # ──────────────────────────────────────────────────────────────
            # Step 4: Parse Docs & Configuration (Module 4)
            # ──────────────────────────────────────────────────────────────
            await self._update_status(
                run,
                step="docs_config",
                progress=35.0,
                message="Parsing documentation and configuration files...",
                stage_index=4,
            )

            parse_docs_func = self.doc_parser.parse
            if inspect.iscoroutinefunction(parse_docs_func):
                docs_knowledge = await parse_docs_func(workspace, inventory)
            else:
                docs_knowledge = await asyncio.to_thread(parse_docs_func, workspace, inventory)

            extract_config_func = self.config_extractor.extract
            if inspect.iscoroutinefunction(extract_config_func):
                config_findings = await extract_config_func(workspace, inventory)
            else:
                config_findings = await asyncio.to_thread(extract_config_func, workspace, inventory)

            # Extract operational knobs from source code (tunables, hazards, timeouts)
            try:
                operational_knobs = await asyncio.to_thread(
                    self.config_extractor.extract_operational_knobs, workspace, inventory
                )
            except Exception as knob_err:
                logger.warning("Error extracting operational knobs: %s", knob_err)
                operational_knobs = []

            # ──────────────────────────────────────────────────────────────
            # Step 5: Static AST Analysis (Module 5)
            # ──────────────────────────────────────────────────────────────
            await self._update_status(
                run,
                step="ast_analysis",
                progress=45.0,
                message="Performing static AST code analysis...",
                stage_index=5,
            )

            static_func = self.static_analysis.analyze
            if inspect.iscoroutinefunction(static_func):
                codebase_ir = await static_func(
                    workspace=workspace,
                    inventory=inventory,
                    profile=profile,
                )
            else:
                codebase_ir = await asyncio.to_thread(
                    static_func,
                    workspace=workspace,
                    inventory=inventory,
                    profile=profile,
                )

            # ──────────────────────────────────────────────────────────────
            # Step 6: Identify Components (Module 6)
            # ──────────────────────────────────────────────────────────────
            await self._update_status(
                run,
                step="components",
                progress=55.0,
                message="Identifying logical application components...",
                stage_index=6,
            )

            components: list[DiscoveredComponent] = []
            self._v2_flows = []

            # ── Strategy: LLM-first grouping ──────────────────────────────
            # Pass all file paths + symbols to LLM in one call.
            # LLM decides component boundaries (no hard-coded rules).
            # Falls back to legacy rule engine if LLM unavailable/fails.
            # ──────────────────────────────────────────────────────────────
            kind_map = {
                "auth": ComponentType.AUTHENTICATION,
                "rate_limiter": ComponentType.RATE_LIMITER,
                "middleware": ComponentType.MIDDLEWARE,
                "api_routing": ComponentType.WEB_TIER,
                "domain_service": ComponentType.DOMAIN_SERVICE,
                "background_worker": ComponentType.BACKGROUND_WORKER,
                "database": ComponentType.DATABASE,
                "cache": ComponentType.CACHE,
                "vector_store": ComponentType.VECTOR_STORE,
                "ai_agent": ComponentType.AGENT,
                "message_queue": ComponentType.MESSAGE_QUEUE,
                "frontend": ComponentType.FRONTEND,
                "ingress": ComponentType.INGRESS,
            }

            llm_groups: list[dict] | None = None
            try:
                from aie.components.llm_grouper import group_by_llm
                llm_groups = await asyncio.to_thread(
                    group_by_llm,
                    codebase_ir,
                    profile,
                )
            except Exception as llm_err:
                logger.warning("[LLMGrouper] LLM grouping raised exception: %s", llm_err, exc_info=True)

            if llm_groups:
                logger.info("[LLMGrouper] LLM discovered %d component groups", len(llm_groups))
                for grp in llm_groups:
                    kind = grp.get("kind", "unknown")
                    comp_type = kind_map.get(kind, ComponentType.SERVICE)
                    responsibility = grp.get("responsibility", "")
                    technologies = grp.get("technologies", [])
                    files = grp.get("files", [])
                    layer = grp.get("layer", "other")

                    dc = DiscoveredComponent(
                        run_id=run_id,
                        name=grp.get("name", "Component"),
                        component_type=comp_type,
                        description=responsibility,
                        technologies=technologies,
                        confidence=float(grp.get("confidence", 0.8)),
                        root_paths=files[:5],
                        layer=layer,
                        metadata={
                            "layer": layer,
                            "kind": kind,
                            "llm_grouped": True,
                            "what_it_is_doing": responsibility,
                            "how_it_is_coded": (
                                f"Built with {', '.join(technologies)}" if technologies else ""
                            ),
                        },
                    )
                    components.append(dc)
            else:
                # ── Fallback: legacy AST rule engine ──────────────────────
                logger.info("[LLMGrouper] LLM unavailable — running legacy rule engine")
                identify_func = self.component_identifier.identify
                if inspect.iscoroutinefunction(identify_func):
                    components = await identify_func(
                        codebase_ir=codebase_ir,
                        profile=profile,
                        docs_knowledge=docs_knowledge,
                        inventory=inventory,
                    )
                else:
                    components = await asyncio.to_thread(
                        identify_func,
                        codebase_ir=codebase_ir,
                        profile=profile,
                        docs_knowledge=docs_knowledge,
                        inventory=inventory,
                    )
                logger.info("[RuleEngine] Rule engine found %d components", len(components))

            for comp in components:
                comp.run_id = run_id

            run.component_count = len(components)
            logger.info("Identified %d components", len(components))

            # ──────────────────────────────────────────────────────────────
            # Step 7: Analyze Relationships (Module 7)
            # ──────────────────────────────────────────────────────────────
            await self._update_status(
                run,
                step="relationships",
                progress=65.0,
                message=f"Analyzing relationships between {len(components)} components...",
                stage_index=7,
            )

            analyze_rel_func = self.relationship_analyzer.analyze
            if inspect.iscoroutinefunction(analyze_rel_func):
                relationships: list[DiscoveredRelationship] = await analyze_rel_func(
                    components=components,
                    codebase_ir=codebase_ir,
                )
            else:
                relationships = await asyncio.to_thread(
                    analyze_rel_func,
                    components=components,
                    codebase_ir=codebase_ir,
                )

            for rel in relationships:
                rel.run_id = run_id

            run.relationship_count = len(relationships)
            logger.info("Analyzed %d relationships", len(relationships))

            # ──────────────────────────────────────────────────────────────
            # Step 8: Extract LLM Knowledge per Component (Module 8)
            # ──────────────────────────────────────────────────────────────
            await self._update_status(
                run,
                step="llm_analysis",
                progress=70.0,
                message="Extracting component knowledge via LLM...",
                stage_index=8,
            )

            # Provide workspace root so LLM gets real source code (not just skeletons)
            if workspace and hasattr(workspace, "root_path"):
                self.context_builder.workspace_root = Path(workspace.root_path)

            if not skip_llm and components:
                total_comps = len(components)
                pool_size = getattr(self.knowledge_extractor, "pool_size", 1) or 1
                concurrency = max(1, min(pool_size, 5))
                sem = asyncio.Semaphore(concurrency)
                completed_count = 0
                lock = asyncio.Lock()

                logger.info(
                    "Running concurrent component knowledge extraction: %d components using pool of %d keys (concurrency=%d)",
                    total_comps,
                    pool_size,
                    concurrency,
                )

                async def _analyze_comp(idx: int, comp: DiscoveredComponent) -> None:
                    nonlocal completed_count
                    async with sem:
                        try:
                            context = self.context_builder.build(
                                component=comp,
                                codebase_ir=codebase_ir,
                                docs_knowledge=docs_knowledge,
                                config_findings=config_findings,
                                all_components=components,
                            )
                            extract_func = self.knowledge_extractor.extract
                            if inspect.iscoroutinefunction(extract_func):
                                knowledge = await extract_func(comp, context)
                            else:
                                knowledge = await asyncio.to_thread(extract_func, comp, context)

                            if knowledge:
                                self._apply_knowledge(comp, knowledge)
                        except Exception as llm_err:
                            logger.warning(
                                "LLM knowledge extraction failed for component '%s': %s",
                                comp.name,
                                llm_err,
                            )
                        finally:
                            async with lock:
                                completed_count += 1
                                current_done = completed_count
                            comp_progress = 70.0 + (current_done / total_comps) * 15.0
                            await self._update_status(
                                run,
                                step="llm_analysis",
                                progress=round(comp_progress, 1),
                                message=f"Analyzed component: {comp.name} ({current_done}/{total_comps})",
                                stage_index=8,
                            )

                await asyncio.gather(*[_analyze_comp(i, c) for i, c in enumerate(components)])

            # Ensure all components have comprehensive operational & diagnostic descriptions
            for comp in components:
                self._ensure_component_description(comp, profile)

            # ──────────────────────────────────────────────────────────────
            # Step 9: Technical Enrichment (Module 9)
            # ──────────────────────────────────────────────────────────────
            await self._update_status(
                run,
                step="enrichment",
                progress=87.0,
                message="Enriching components with domain and technology knowledge...",
                stage_index=9,
            )

            enrich_res = self.enricher.enrich(components, profile)
            if inspect.isawaitable(enrich_res):
                await enrich_res

            # ──────────────────────────────────────────────────────────────
            # Step 10: Validate Evidence & Confidence (Module 10)
            # ──────────────────────────────────────────────────────────────
            await self._update_status(
                run,
                step="validation",
                progress=90.0,
                message="Validating evidence sources and calculating confidence...",
                stage_index=10,
            )

            validate_res = self.evidence_manager.validate(components, relationships)
            if inspect.isawaitable(validate_res):
                await validate_res

            # ──────────────────────────────────────────────────────────────
            # Step 11: Generate Graph, Descriptor & Capabilities (Module 11)
            # ──────────────────────────────────────────────────────────────
            await self._update_status(
                run,
                step="graph",
                progress=93.0,
                message="Constructing architecture graph and contract descriptors...",
                stage_index=11,
            )

            graph: ArchitectureGraph = self.graph_generator.generate(components, relationships)
            descriptor: dict[str, Any] = self.descriptor_generator.generate_descriptor(
                app_id=app_id,
                components=components,
                relationships=relationships,
                profile=profile,
                flows=getattr(self, "_v2_flows", None),
            )
            if operational_knobs:
                descriptor["operational_knobs"] = operational_knobs
            capabilities: dict[str, Any] = self.descriptor_generator.infer_capabilities(
                components=components,
                app_id=app_id,
            )

            # ──────────────────────────────────────────────────────────────
            # Step 12: Knowledge Persistence & Vector Index (Module 12)
            # ──────────────────────────────────────────────────────────────
            await self._update_status(
                run,
                step="persisting",
                progress=97.0,
                message="Persisting knowledge artifacts and indexing embeddings...",
                stage_index=12,
            )

            # Store in in-memory store (backwards compatibility)
            self.store.store_all(
                run=run,
                inventory=inventory,
                profile=profile,
                components=components,
                relationships=relationships,
                graph=graph,
                descriptor=descriptor,
                capabilities=capabilities,
            )

            # Multi-tier Knowledge Persistence: MongoDB (Source of Truth), Redis (In-Memory Graph), ChromaDB (Vectors)
            try:
                from aie.persistence.services import persistence_service
                await persistence_service.save_completed_analysis(
                    run=run,
                    inventory=inventory,
                    profile=profile,
                    components=components,
                    relationships=relationships,
                    graph=graph,
                    descriptor=descriptor,
                    capabilities=capabilities,
                )
            except Exception as persist_err:
                logger.warning("KnowledgePersistenceService error (non-fatal): %s", persist_err)

            # Index components in vector store
            for comp in components:
                self.vector_store.add_component(comp)

            # ──────────────────────────────────────────────────────────────
            # Complete
            # ──────────────────────────────────────────────────────────────
            run.status = "complete"
            run.completed_at = datetime.now(tz=timezone.utc)
            run.result_summary = {
                "file_count": len(inventory),
                "component_count": len(components),
                "relationship_count": len(relationships),
                "action_count": len(capabilities.get("actions", [])),
                "node_count": len(graph.nodes),
                "edge_count": len(graph.edges),
            }
            self.store.store_analysis(run)

            await self._update_status(
                run,
                step="complete",
                progress=100.0,
                message=f"Analysis complete: {len(components)} components, {len(relationships)} relationships",
                stage_index=12,
            )
            logger.info("Pipeline analysis completed successfully for run %s", run_id)

        except asyncio.CancelledError:
            run.status = "failed"
            run.error_message = "Analysis was cancelled"
            run.completed_at = datetime.now(tz=timezone.utc)
            self.store.store_analysis(run)
            raise

        except Exception as exc:
            logger.exception("Analysis pipeline failed for run %s: %s", run_id, exc)
            run.status = "failed"
            run.completed_at = datetime.now(tz=timezone.utc)
            run.error_message = str(exc)
            self.store.store_analysis(run)

            await self._update_status(
                run,
                step="failed",
                progress=0.0,
                message=f"Analysis failed: {exc}",
                stage_index=0,
            )

        finally:
            # Always clean up workspace
            if workspace is not None:
                try:
                    cleanup_func = self.ingestion.cleanup
                    if inspect.iscoroutinefunction(cleanup_func):
                        await cleanup_func(workspace)
                    else:
                        await asyncio.to_thread(cleanup_func, workspace)
                    logger.info("Cleaned up workspace for run %s", run_id)
                except Exception as cleanup_err:
                    logger.warning(
                        "Error cleaning up workspace %s: %s",
                        getattr(workspace, "root_path", "unknown"),
                        cleanup_err,
                    )

            # Signal end of stream to WebSocket subscribers
            await self.event_bus.publish_done(run_id)

        return run

    def _ensure_component_description(
        self,
        component: DiscoveredComponent,
        profile: TechnologyProfile | None = None,
    ) -> None:
        """Guarantee every component has a complete operational, code, and diagnostic explanation."""
        if not component.description:
            tech_str = ", ".join(component.technologies) if component.technologies else (
                getattr(profile, "primary_language", None) or "software"
            )
            paths_str = ", ".join(component.root_paths) if component.root_paths else "root directory"
            doing = f"Manages {component.name} operations, serving as the {component.component_type} layer to process domain logic and workflows."
            coded = f"Built with {tech_str} located in {paths_str}, structuring business logic and interfaces to encapsulate component behaviors."
            diagnostics = (
                f"When issues occur in {component.name}: 1) Inspect error logs under {paths_str}. "
                f"2) Verify network and service dependencies for {component.name}. "
                f"3) Check resource utilization and connection pool thresholds."
            )
            component.description = (
                f"WHAT IT IS DOING:\n{doing}\n\n"
                f"HOW IT IS CODED:\n{coded}\n\n"
                f"DIAGNOSTIC GUIDE:\n{diagnostics}"
            )
            component.metadata.setdefault("what_it_is_doing", doing)
            component.metadata.setdefault("how_it_is_coded", coded)
            component.metadata.setdefault("diagnostic_guide", diagnostics)

    def _apply_knowledge(self, component: DiscoveredComponent, knowledge: dict[str, Any]) -> None:
        """Apply LLM-extracted knowledge to a component."""
        if not knowledge:
            return

        if hasattr(component, "update_with_knowledge"):
            component.update_with_knowledge(knowledge)
            return

        # Direct attribution: set clear, comprehensive description
        if knowledge.get("description"):
            component.description = knowledge["description"]
        elif knowledge.get("responsibility") and knowledge.get("implementation_summary"):
            component.description = (
                f"What it is doing: {knowledge['responsibility']}\n\n"
                f"How it is coded: {knowledge['implementation_summary']}"
            )

        if knowledge.get("responsibility"):
            component.metadata["what_it_is_doing"] = knowledge["responsibility"]
        if knowledge.get("implementation_summary"):
            component.metadata["how_it_is_coded"] = knowledge["implementation_summary"]

        if knowledge.get("technologies"):
            for tech in knowledge["technologies"]:
                if tech not in component.technologies:
                    component.technologies.append(tech)

        if hasattr(component, "responsibility") and knowledge.get("responsibility"):
            component.responsibility = knowledge["responsibility"]

        if hasattr(component, "failure_scenarios") and knowledge.get("failure_scenarios"):
            component.failure_scenarios = list(
                set(component.failure_scenarios + knowledge["failure_scenarios"])
            )

        if hasattr(component, "monitoring_requirements") and knowledge.get("monitoring_requirements"):
            component.monitoring_requirements = list(
                set(component.monitoring_requirements + knowledge["monitoring_requirements"])
            )

        if hasattr(component, "adaptation_opportunities") and knowledge.get("adaptation_opportunities"):
            if hasattr(component.adaptation_opportunities, "extend"):
                component.adaptation_opportunities.extend(knowledge["adaptation_opportunities"])

        if knowledge.get("confidence") and hasattr(component, "_llm_confidence"):
            component._llm_confidence = knowledge["confidence"]

        component.metadata.update(knowledge)
