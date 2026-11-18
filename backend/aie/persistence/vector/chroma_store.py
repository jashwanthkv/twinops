"""
aie/persistence/vector/chroma_store.py — ChromaDB Semantic Knowledge Retrieval.

Indexes semantic chunks (component responsibilities, failure scenarios, monitoring requirements,
adaptation opportunities) using sentence-transformers/all-MiniLM-L6-v2 without duplicating
structured documents.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from aie.models import DiscoveredComponent

# Telemetry disabled
os.environ["ANONYMIZED_TELEMETRY"] = "False"
os.environ["CHROMA_TELEMETRY"] = "False"

logger = logging.getLogger(__name__)


class ChromaKnowledgeStore:
    """Version-aware semantic retrieval index using ChromaDB."""

    COLLECTION_NAME = "aie_knowledge_embeddings"

    def __init__(self) -> None:
        self._client: Any = None
        self._collection: Any = None
        self._is_available: bool = False
        self._initialized: bool = False

    def _ensure_init(self) -> bool:
        if self._initialized:
            return self._is_available

        self._initialized = True
        try:
            import chromadb
            from chromadb.config import Settings

            # Local model path
            local_model_path = Path(__file__).resolve().parent.parent.parent.parent / "models" / "all-MiniLM-L6-v2"
            embedding_func = None
            if local_model_path.exists():
                try:
                    from chromadb.utils.embedding_functions.onnx_mini_lm_l6_v2 import ONNXMiniLM_L6_V2
                    ONNXMiniLM_L6_V2.DOWNLOAD_PATH = local_model_path
                    embedding_func = ONNXMiniLM_L6_V2()
                    logger.info("ChromaDB ONNX model pinned to local directory: %s", local_model_path)
                except Exception as ef_err:
                    logger.debug("Could not override ONNXMiniLM_L6_V2 path: %s", ef_err)

            self._client = chromadb.EphemeralClient(
                settings=Settings(anonymized_telemetry=False)
            )
            self._collection = self._client.get_or_create_collection(
                name=self.COLLECTION_NAME,
                embedding_function=embedding_func,
                metadata={"hnsw:space": "cosine"},
            )
            self._is_available = True
            logger.info("ChromaDB knowledge collection initialized successfully")
        except Exception as exc:
            logger.warning("ChromaDB unavailable (%s). Semantic search running in no-op mode.", exc)
            self._is_available = False

        return self._is_available

    def index_component_knowledge(
        self,
        app_id: str,
        run_id: str,
        version: int,
        components: list[DiscoveredComponent | dict[str, Any]],
    ) -> int:
        """Decompose component knowledge into semantic chunks and index in ChromaDB."""
        if not self._ensure_init() or self._collection is None:
            return 0

        documents: list[str] = []
        metadatas: list[dict[str, Any]] = []
        ids: list[str] = []

        chunk_counter = 0

        for comp in components:
            cid = comp.component_id if isinstance(comp, DiscoveredComponent) else comp.get("component_id") or comp.get("name")
            cname = comp.name if isinstance(comp, DiscoveredComponent) else comp.get("name", cid)
            primary_tech = comp.technologies[0] if (isinstance(comp, DiscoveredComponent) and comp.technologies) else (comp.get("technologies", ["Unknown"])[0] if isinstance(comp, dict) and comp.get("technologies") else "Unknown")
            source_file = "Unknown"
            if isinstance(comp, DiscoveredComponent):
                if comp.root_paths:
                    source_file = comp.root_paths[0]
                elif getattr(comp, "evidence", None) and comp.evidence[0].source_path:
                    source_file = comp.evidence[0].source_path
            elif isinstance(comp, dict):
                paths = comp.get("root_paths") or comp.get("file_paths") or []
                if paths:
                    source_file = paths[0]

            base_meta = {
                "application_id": str(app_id),
                "analysis_run_id": str(run_id),
                "analysis_version": int(version),
                "component_id": str(cid),
                "technology": str(primary_tech),
                "source_file": str(source_file),
            }

            # Helper to safely retrieve attributes or metadata values
            def _get_val(key: str, default: Any = None) -> Any:
                if isinstance(comp, dict):
                    return comp.get(key, default)
                val = getattr(comp, key, None)
                if val is not None:
                    return val
                meta = getattr(comp, "metadata", {})
                if isinstance(meta, dict) and key in meta:
                    return meta[key]
                return default

            # 1. Responsibility Chunk
            resp = _get_val("responsibility") or _get_val("description")
            if resp:
                chunk_counter += 1
                doc_id = f"{app_id}:{run_id}:{cid}:resp:{chunk_counter}"
                text = f"Component '{cname}' Architectural Responsibility: {resp}"
                ids.append(doc_id)
                documents.append(text)
                metadatas.append({**base_meta, "content_type": "responsibility"})

            # 2. Implementation Summary Chunk
            impl = _get_val("implementation_summary")
            if impl:
                chunk_counter += 1
                doc_id = f"{app_id}:{run_id}:{cid}:impl:{chunk_counter}"
                text = f"Component '{cname}' Implementation Details: {impl}"
                ids.append(doc_id)
                documents.append(text)
                metadatas.append({**base_meta, "content_type": "implementation"})

            # 3. Failure Scenarios Chunks
            scenarios = _get_val("failure_scenarios", [])
            for s_idx, scenario in enumerate(scenarios or []):
                chunk_counter += 1
                doc_id = f"{app_id}:{run_id}:{cid}:fail:{s_idx}:{chunk_counter}"
                text = f"Component '{cname}' Potential Failure Scenario: {scenario}"
                ids.append(doc_id)
                documents.append(text)
                metadatas.append({**base_meta, "content_type": "failure_scenario"})

            # 4. Monitoring Requirements Chunks
            metrics = _get_val("monitoring_requirements", [])
            for m_idx, metric in enumerate(metrics or []):
                chunk_counter += 1
                doc_id = f"{app_id}:{run_id}:{cid}:mon:{m_idx}:{chunk_counter}"
                text = f"Component '{cname}' Monitoring Requirement & Metric: {metric}"
                ids.append(doc_id)
                documents.append(text)
                metadatas.append({**base_meta, "content_type": "monitoring"})

            # 5. Adaptation Opportunities Chunks
            adapts = _get_val("adaptation_opportunities", [])
            for a_idx, adapt in enumerate(adapts or []):
                chunk_counter += 1
                doc_id = f"{app_id}:{run_id}:{cid}:adapt:{a_idx}:{chunk_counter}"
                text = f"Component '{cname}' Adaptation Operational Lever: {adapt}"
                ids.append(doc_id)
                documents.append(text)
                metadatas.append({**base_meta, "content_type": "adaptation"})

        if ids:
            try:
                self._collection.upsert(
                    ids=ids,
                    documents=documents,
                    metadatas=metadatas,
                )
                logger.info("Indexed %d semantic knowledge chunks in ChromaDB for app '%s'", len(ids), app_id)
                return len(ids)
            except Exception as exc:
                logger.warning("Error indexing chunks in ChromaDB: %s", exc)

        return 0

    def semantic_search(
        self,
        app_id: str,
        query: str,
        run_id: str | None = None,
        component_id: str | None = None,
        content_type: str | None = None,
        n_results: int = 5,
    ) -> list[dict[str, Any]]:
        """Perform semantic search filtered by application and active run ID."""
        if not self._ensure_init() or self._collection is None:
            return []

        # Construct compound where filter
        filter_conditions: list[dict[str, Any]] = [{"application_id": {"$eq": str(app_id)}}]
        if run_id:
            filter_conditions.append({"analysis_run_id": {"$eq": str(run_id)}})
        if component_id:
            filter_conditions.append({"component_id": {"$eq": str(component_id)}})
        if content_type:
            filter_conditions.append({"content_type": {"$eq": str(content_type)}})

        where_clause: dict[str, Any]
        if len(filter_conditions) == 1:
            where_clause = filter_conditions[0]
        else:
            where_clause = {"$and": filter_conditions}

        try:
            results = self._collection.query(
                query_texts=[query],
                n_results=n_results,
                where=where_clause,
            )

            hits = []
            if results and results.get("documents") and results["documents"]:
                docs = results["documents"][0]
                metas = results.get("metadatas", [[]])[0]
                distances = results.get("distances", [[]])[0] if results.get("distances") else [0.0] * len(docs)

                for doc, meta, dist in zip(docs, metas, distances):
                    score = round(1.0 - dist, 4) if dist is not None else 1.0
                    hits.append({
                        "content": doc,
                        "metadata": meta,
                        "similarity_score": score,
                    })

            return hits
        except Exception as exc:
            logger.warning("Error querying ChromaDB: %s", exc)
            return []

    def delete_run_embeddings(self, app_id: str, run_id: str) -> None:
        """Clean up embeddings when deleting an analysis version."""
        if not self._ensure_init() or self._collection is None:
            return
        try:
            self._collection.delete(
                where={"$and": [{"application_id": str(app_id)}, {"analysis_run_id": str(run_id)}]}
            )
            logger.info("Deleted embeddings for run '%s' (app '%s')", run_id, app_id)
        except Exception as exc:
            logger.debug("Could not delete embeddings: %s", exc)


chroma_store = ChromaKnowledgeStore()
