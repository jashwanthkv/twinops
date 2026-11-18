"""Rule for detecting relational or NoSQL database components."""

from __future__ import annotations

from typing import Any
from aie.components.scoring import ComponentCandidate
from aie.models import ComponentType, EvidenceType, SourceEvidence, TechnologyProfile
from aie.static_analysis.ir import CodebaseIR


class DatabaseRule:
    """Detects database services via connection engines, pools, DATABASE_URL config,

    and compose/config definitions.
    """

    _ENGINE_CALLS = {
        "create_engine", "create_async_engine", "create_pool", "connect",
        "getConnection", "DriverManager.getConnection",
    }

    _DB_DIALECT_KEYWORDS = {
        "postgres": "PostgreSQL",
        "postgresql": "PostgreSQL",
        "psycopg2": "PostgreSQL",
        "asyncpg": "PostgreSQL",
        "mysql": "MySQL",
        "mariadb": "MariaDB",
        "sqlite": "SQLite",
        "mongodb": "MongoDB",
        "mongo": "MongoDB",
        "oracle": "Oracle",
        "mssql": "SQL Server",
    }

    def detect(
        self,
        codebase_ir: CodebaseIR,
        profile: TechnologyProfile | None = None,
        docs_knowledge: Any = None,
    ) -> list[ComponentCandidate]:
        db_files: set[str] = set()
        evidence_list: list[SourceEvidence] = []
        technologies_detected: set[str] = set()
        has_engine_call = False
        has_imports = False
        config_detected = False

        for f in codebase_ir.files:
            file_has_db = False

            # 1. Framework patterns (e.g. asyncpg.connect, psycopg2.connect, sqlalchemy create_engine)
            for p in f.framework_patterns:
                if p.type in {"database", "orm"}:
                    file_has_db = True
                    has_engine_call = True
                    fw_lower = p.framework.lower()
                    for kw, name in self._DB_DIALECT_KEYWORDS.items():
                        if kw in fw_lower:
                            technologies_detected.add(name)
                    evidence_list.append(
                        SourceEvidence(
                            evidence_type=EvidenceType.FILE_CONTENT,
                            source_path=f.file_path,
                            snippet=f"{p.framework} pattern: {p.details.get('call', '')}",
                            confidence=0.9,
                            notes=f"Database connection initialization in {f.file_path}:{p.line}",
                        )
                    )

            # 2. Check call expressions for create_engine, Pool, etc.
            all_calls = list(f.global_calls)
            for fn in f.functions:
                all_calls.extend(fn.calls)

            for call in all_calls:
                callee_lower = call.callee.lower()
                for ec in self._ENGINE_CALLS:
                    if callee_lower.endswith(ec.lower()) or ec.lower() in callee_lower:
                        file_has_db = True
                        has_engine_call = True
                        for kw, name in self._DB_DIALECT_KEYWORDS.items():
                            if kw in callee_lower:
                                technologies_detected.add(name)
                        evidence_list.append(
                            SourceEvidence(
                                evidence_type=EvidenceType.FILE_CONTENT,
                                source_path=f.file_path,
                                snippet=f"Call: {call.callee}(...)",
                                confidence=0.9,
                                notes=f"Database engine/pool call in {f.file_path}:{call.line}",
                            )
                        )

            # 3. Check imports
            for imp in f.imports:
                mod = (imp.module or imp.from_module or "").lower()
                for kw, name in self._DB_DIALECT_KEYWORDS.items():
                    if kw in mod:
                        has_imports = True
                        technologies_detected.add(name)

            if file_has_db:
                db_files.add(f.file_path)

        # Cross-reference with technology profile
        if profile and hasattr(profile, "technologies"):
            for t in profile.technologies:
                if t.category.lower() in {"database", "data_store"}:
                    technologies_detected.add(t.name)
                    evidence_list.append(
                        SourceEvidence(
                            evidence_type=EvidenceType.HEURISTIC,
                            source_path="technology_profile",
                            snippet=f"Detected technology: {t.name} ({t.category})",
                            confidence=t.confidence,
                            notes="Confirmed database technology from profile",
                        )
                    )

        # Cross-reference with docs / config knowledge
        doc_score = 0.0
        if docs_knowledge:
            docs_text = str(docs_knowledge).lower()
            if any(term in docs_text for term in ["database_url", "postgres", "mysql", "mongodb", "sqlite", "datasource"]):
                config_detected = True
                doc_score = 0.8
                for kw, name in self._DB_DIALECT_KEYWORDS.items():
                    if kw in docs_text:
                        technologies_detected.add(name)

        if not has_engine_call and not technologies_detected and not config_detected:
            return []

        primary_tech = "Database"
        if technologies_detected:
            # Pick the most specific detected DB (e.g. PostgreSQL over generic Database)
            primary_tech = sorted(list(technologies_detected))[0]

        code_behavior_score = 0.9 if has_engine_call else 0.4
        imports_score = 0.85 if has_imports else 0.4
        config_score = 0.85 if config_detected else 0.5
        convention_score = 0.7 if len(db_files) > 0 else 0.4

        candidate = ComponentCandidate(
            name=f"{primary_tech} Database",
            type=ComponentType.DATABASE,
            technology=primary_tech,
            evidence=evidence_list[:15],
            files=sorted(list(db_files)),
            score_evidence={
                "imports": imports_score,
                "code_behavior": code_behavior_score,
                "config": config_score,
                "framework_convention": convention_score,
                "docs": doc_score,
            },
            metadata={
                "connection_files_count": len(db_files),
                "technologies": sorted(list(technologies_detected)),
            },
        )

        return [candidate]
