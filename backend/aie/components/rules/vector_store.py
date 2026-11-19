"""Rule for detecting vector store and embedding components."""

from __future__ import annotations

from typing import Any
from aie.components.scoring import ComponentCandidate
from aie.models import ComponentType, EvidenceType, SourceEvidence, TechnologyProfile
from aie.static_analysis.ir import CodebaseIR


class VectorStoreRule:
    """Detects vector/embedding store components.

    Signals: chromadb, faiss, pinecone, weaviate, qdrant, sentence-transformers imports;
    similarity_search, embed, upsert, query calls; vector_store/ directory; chroma/faiss
    variable/class names.
    """

    _VECTOR_IMPORTS = {
        "chromadb", "chroma", "faiss", "pinecone", "weaviate", "qdrant_client",
        "qdrant", "milvus", "pgvector", "redis.commands.search",
        "sentence_transformers", "sentence-transformers",
        "transformers", "huggingface_hub",
    }
    _EMBED_CALLS = {
        "similarity_search", "similarity_search_with_score",
        "embed", "embed_documents", "embed_query",
        "upsert", "query", "search", "encode",
        "from_documents", "from_texts", "add_texts", "add_documents",
        "PersistentClient", "Client", "EmbeddingFunction",
        "HuggingFaceEmbeddings", "SentenceTransformer",
    }
    _VECTOR_DIR_KEYWORDS = {"vector_store", "vectore_store", "embeddings", "vectors", "chroma"}

    def detect(
        self,
        codebase_ir: CodebaseIR,
        profile: TechnologyProfile | None = None,
        docs_knowledge: Any = None,
    ) -> list[ComponentCandidate]:
        vector_files: set[str] = set()
        evidence_list: list[SourceEvidence] = []
        technologies: set[str] = set()
        has_imports = False
        has_calls = False

        for f in codebase_ir.files:
            file_path_lower = f.file_path.replace("\\", "/").lower()
            path_parts = set(file_path_lower.split("/"))

            # 1. Directory heuristic — vector_store/, embeddings/, etc.
            if path_parts & self._VECTOR_DIR_KEYWORDS:
                vector_files.add(f.file_path)

            # 2. Imports
            for imp in f.imports:
                mod = (imp.module or imp.from_module or "").lower()
                root_mod = mod.split(".")[0]
                if root_mod in self._VECTOR_IMPORTS or any(v in mod for v in self._VECTOR_IMPORTS):
                    has_imports = True
                    if "chroma" in mod:
                        technologies.add("ChromaDB")
                    elif "faiss" in mod:
                        technologies.add("FAISS")
                    elif "pinecone" in mod:
                        technologies.add("Pinecone")
                    elif "qdrant" in mod:
                        technologies.add("Qdrant")
                    elif "weaviate" in mod:
                        technologies.add("Weaviate")
                    elif "sentence_transformer" in mod or "sentence-transformer" in mod:
                        technologies.add("SentenceTransformers")
                    elif "huggingface" in mod or "transformers" in mod:
                        technologies.add("HuggingFace")
                    else:
                        technologies.add(root_mod.capitalize())
                    vector_files.add(f.file_path)
                    evidence_list.append(
                        SourceEvidence(
                            evidence_type=EvidenceType.FILE_CONTENT,
                            source_path=f.file_path,
                            snippet=f"Vector/embedding import: {imp.module or imp.from_module}",
                            confidence=0.85,
                            notes=f"Vector store library in {f.file_path}:{imp.line}",
                        )
                    )

            # 3. Calls
            all_calls = list(f.global_calls)
            for fn in f.functions:
                all_calls.extend(fn.calls)
            for call in all_calls:
                func_name = call.callee.split(".")[-1]
                if func_name in self._EMBED_CALLS:
                    has_calls = True
                    vector_files.add(f.file_path)
                    evidence_list.append(
                        SourceEvidence(
                            evidence_type=EvidenceType.FILE_CONTENT,
                            source_path=f.file_path,
                            snippet=f"Vector op: {call.callee}(...) at line {call.line}",
                            confidence=0.8,
                            notes=f"Embedding/vector search call at {f.file_path}:{call.line}",
                        )
                    )

        if not vector_files:
            return []

        primary_tech = "ChromaDB" if "ChromaDB" in technologies else (
            sorted(list(technologies))[0] if technologies else "VectorStore"
        )

        return [ComponentCandidate(
            name=f"{primary_tech} Vector Store",
            type=ComponentType.VECTOR_STORE,
            technology=primary_tech,
            evidence=evidence_list[:12],
            files=sorted(list(vector_files)),
            score_evidence={
                "imports": 0.9 if has_imports else 0.4,
                "code_behavior": 0.85 if has_calls else 0.5,
                "config": 0.5,
                "framework_convention": 0.7,
                "docs": 0.0,
            },
            metadata={
                "technologies": sorted(list(technologies)),
                "file_count": len(vector_files),
            },
        )]
