"""
aie/persistence/vector_store.py — Module 12: Ephemeral Vector Store.

In-process vector embeddings for discovered components using ChromaDB EphemeralClient.
Gracefully falls back to no-op if chromadb is unavailable or fails.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from aie.models import DiscoveredComponent

# Disable ChromaDB telemetry in all environments
os.environ["ANONYMIZED_TELEMETRY"] = "False"
os.environ["CHROMA_TELEMETRY"] = "False"

logger = logging.getLogger(__name__)


class VectorStore:
    """Ephemeral vector database wrapper using ChromaDB with lazy initialization."""

    def __init__(self) -> None:
        self._client: Any = None
        self._collection: Any = None
        self._is_available: bool = False
        self._initialized: bool = False

    def _ensure_init(self) -> bool:
        if self._initialized:
            return self._is_available

        self._initialized = True
        if os.environ.get("AIE_DISABLE_CHROMA", "").lower() in ("1", "true"):
            self._is_available = False
            return False

        try:
            from pathlib import Path
            import chromadb
            from chromadb.config import Settings

            # Ensure ChromaDB uses the locally stored all-MiniLM-L6-v2 model and never re-downloads
            local_model_path = Path(__file__).resolve().parent.parent.parent / "models" / "all-MiniLM-L6-v2"
            if local_model_path.exists():
                try:
                    from chromadb.utils.embedding_functions.onnx_mini_lm_l6_v2 import ONNXMiniLM_L6_V2
                    ONNXMiniLM_L6_V2.DOWNLOAD_PATH = local_model_path
                    logger.info("ChromaDB ONNX model pinned to local directory: %s", local_model_path)
                except Exception as ef_err:
                    logger.debug("Could not override ONNXMiniLM_L6_V2 path: %s", ef_err)

            self._client = chromadb.EphemeralClient(
                settings=Settings(anonymized_telemetry=False)
            )
            self._collection = self._client.get_or_create_collection(
                name="aie_components",
                metadata={"hnsw:space": "cosine"},
            )
            self._is_available = True
            logger.info("ChromaDB EphemeralClient initialized successfully")
        except Exception as exc:
            logger.warning(
                "ChromaDB not available or failed to initialize: %s. VectorStore will run in no-op mode.",
                exc,
            )
            self._is_available = False

        return self._is_available

    @property
    def is_available(self) -> bool:
        """Return True if ChromaDB is active."""
        return self._ensure_init()

    def add_component(self, component: DiscoveredComponent) -> None:
        """Add component description and metadata to vector index."""
        if not self._ensure_init() or self._collection is None:
            logger.debug("VectorStore no-op: cannot add component %s", component.component_id)
            return

        try:
            # Construct comprehensive textual representation for embedding
            text_lines = [
                f"Component Name: {component.name}",
                f"Component Type: {component.component_type}",
                f"Primary Language: {component.primary_language or 'none'}",
                f"Technologies: {', '.join(component.technologies) if component.technologies else 'none'}",
                f"Description: {component.description or 'No description provided'}",
            ]
            if component.root_paths:
                text_lines.append(f"Root Directories: {', '.join(component.root_paths)}")
            if component.exposed_ports:
                text_lines.append(f"Exposed Ports: {', '.join(map(str, component.exposed_ports))}")

            document_content = "\n".join(text_lines)

            metadata: dict[str, Any] = {
                "component_id": component.component_id,
                "name": component.name,
                "run_id": component.run_id,
                "component_type": (
                    component.component_type.value
                    if hasattr(component.component_type, "value")
                    else str(component.component_type)
                ),
                "confidence": float(component.confidence),
            }

            self._collection.upsert(
                ids=[component.component_id],
                documents=[document_content],
                metadatas=[metadata],
            )
            logger.debug("Indexed component %s into ChromaDB", component.component_id)
        except Exception as exc:
            logger.warning("Failed to index component %s in ChromaDB: %s", component.component_id, exc)

    def search_similar(self, query: str, n_results: int = 5) -> list[dict[str, Any]]:
        """Search for components semantically similar to *query*.

        Returns a list of dicts with keys: id, document, metadata, distance.
        """
        if not self._ensure_init() or self._collection is None:
            logger.debug("VectorStore no-op: returning empty search results")
            return []

        try:
            count = self._collection.count()
            if count == 0:
                return []

            actual_n = min(n_results, count)
            results = self._collection.query(
                query_texts=[query],
                n_results=actual_n,
            )

            hits: list[dict[str, Any]] = []
            ids = results.get("ids", [[]])[0]
            documents = results.get("documents", [[]])[0]
            metadatas = results.get("metadatas", [[]])[0]
            distances = results.get("distances", [[]])[0] if "distances" in results else [0.0] * len(ids)

            for idx, item_id in enumerate(ids):
                hits.append(
                    {
                        "id": item_id,
                        "document": documents[idx] if idx < len(documents) else "",
                        "metadata": metadatas[idx] if idx < len(metadatas) else {},
                        "distance": distances[idx] if idx < len(distances) else 0.0,
                    }
                )

            return hits
        except Exception as exc:
            logger.warning("ChromaDB query failed: %s", exc)
            return []
