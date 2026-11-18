"""Rule for detecting data access and ORM persistence components."""

from __future__ import annotations

from typing import Any
from aie.components.scoring import ComponentCandidate
from aie.models import ComponentType, EvidenceType, SourceEvidence, TechnologyProfile
from aie.static_analysis.ir import CodebaseIR


class DataAccessRule:
    """Detects Data Access components such as ORM models, Repositories, DAOs, and DB sessions."""

    _ORM_BASES = {
        "Base", "DeclarativeBase", "Model", "db.Model", "Document", "AsyncAttrs",
        "CrudRepository", "JpaRepository", "MongoRepository", "AbstractRepository",
    }
    _ENTITY_DECORATORS = {"Entity", "Table", "Repository", "Document", "dataclass"}

    def detect(
        self,
        codebase_ir: CodebaseIR,
        profile: TechnologyProfile | None = None,
        docs_knowledge: Any = None,
    ) -> list[ComponentCandidate]:
        dao_files: set[str] = set()
        evidence_list: list[SourceEvidence] = []
        technologies_detected: set[str] = set()
        has_imports = False

        for f in codebase_ir.files:
            file_has_dao = False

            # 1. ORM patterns from static analysis
            for p in f.framework_patterns:
                if p.type in {"orm", "database"}:
                    file_has_dao = True
                    technologies_detected.add(p.framework)
                    evidence_list.append(
                        SourceEvidence(
                            evidence_type=EvidenceType.FILE_CONTENT,
                            source_path=f.file_path,
                            snippet=f"{p.framework} ORM pattern: {p.details.get('call', '')}",
                            confidence=0.9,
                            notes=f"Detected ORM/database pattern in {f.file_path}:{p.line}",
                        )
                    )

            # 2. Check classes for ORM inheritance or Repository/DAO naming
            for cls in f.classes:
                is_orm_model = any(
                    any(base_name in b for base_name in self._ORM_BASES)
                    for b in cls.bases
                )
                is_repo_name = (
                    "repository" in cls.name.lower()
                    or "dao" in cls.name.lower()
                    or "model" in cls.name.lower()
                )
                has_repo_dec = any(
                    dec.split(".")[-1] in self._ENTITY_DECORATORS for dec in cls.decorators
                )

                if is_orm_model or is_repo_name or has_repo_dec:
                    file_has_dao = True
                    reason = (
                        f"ORM Model extending {cls.bases}"
                        if is_orm_model
                        else ("Repository class" if is_repo_name else "Entity decorator")
                    )
                    evidence_list.append(
                        SourceEvidence(
                            evidence_type=EvidenceType.FILE_CONTENT,
                            source_path=f.file_path,
                            snippet=f"class {cls.name}({', '.join(cls.bases)}) [{reason}]",
                            confidence=0.85,
                            notes=f"Data access structure at {f.file_path}:{cls.line}",
                        )
                    )

            # 3. Check imports for ORM/DB libraries
            for imp in f.imports:
                mod = (imp.module or imp.from_module or "").lower()
                if any(
                    db_lib in mod
                    for db_lib in [
                        "sqlalchemy",
                        "mongoose",
                        "prisma",
                        "typeorm",
                        "sequelize",
                        "peewee",
                        "tortoise",
                        "hibernate",
                    ]
                ):
                    has_imports = True
                    if "sqlalchemy" in mod:
                        technologies_detected.add("SQLAlchemy")
                    elif "mongoose" in mod:
                        technologies_detected.add("Mongoose")
                    elif "prisma" in mod:
                        technologies_detected.add("Prisma")
                    elif "sequelize" in mod:
                        technologies_detected.add("Sequelize")

            if file_has_dao:
                dao_files.add(f.file_path)

        if not dao_files and not evidence_list:
            return []

        primary_tech = "ORM / Repository"
        if technologies_detected:
            primary_tech = sorted(list(technologies_detected))[0]
        elif profile and hasattr(profile, "technologies"):
            for t in profile.technologies:
                if t.category.lower() in {"database", "orm"}:
                    primary_tech = t.name
                    break

        doc_score = 0.0
        if docs_knowledge:
            docs_text = str(docs_knowledge).lower()
            if any(term in docs_text for term in ["repository", "dao", "orm", "schema", "model", "migration"]):
                doc_score = 0.8

        num_files = len(dao_files)
        code_behavior_score = min(1.0, 0.6 + 0.05 * len(evidence_list))
        imports_score = 0.9 if has_imports else 0.5
        convention_score = min(1.0, 0.6 + 0.1 * num_files)
        config_score = 0.6

        candidate = ComponentCandidate(
            name=f"{primary_tech} Data Access Layer",
            type=ComponentType.DATA_ACCESS,
            technology=primary_tech,
            evidence=evidence_list[:15],
            files=sorted(list(dao_files)),
            score_evidence={
                "imports": imports_score,
                "code_behavior": code_behavior_score,
                "config": config_score,
                "framework_convention": convention_score,
                "docs": doc_score,
            },
            metadata={
                "model_files_count": num_files,
                "evidence_count": len(evidence_list),
                "technologies": sorted(list(technologies_detected)),
            },
        )

        return [candidate]
