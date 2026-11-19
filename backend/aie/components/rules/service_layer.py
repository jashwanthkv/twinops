"""Rule for detecting service layer, manager, handler, and runner components."""

from __future__ import annotations

from typing import Any
from aie.components.scoring import ComponentCandidate
from aie.models import ComponentType, EvidenceType, SourceEvidence, TechnologyProfile
from aie.static_analysis.ir import CodebaseIR


class ServiceLayerRule:
    """Detects service layer components: manager classes, service files, task runners,
    and business logic orchestrators that are not pure web endpoints or DB models.

    Signals: class names ending in Service/Manager/Handler/Runner/Processor;
    files in services/ directory; classes with many cross-component method calls.
    """

    _SERVICE_CLASS_SUFFIXES = {
        "service", "manager", "handler", "runner", "processor",
        "controller", "coordinator", "dispatcher", "executor",
    }
    _SERVICE_DIR_KEYWORDS = {"services", "service", "managers", "handlers", "runners"}
    _MIN_METHODS_FOR_SERVICE = 3

    def detect(
        self,
        codebase_ir: CodebaseIR,
        profile: TechnologyProfile | None = None,
        docs_knowledge: Any = None,
    ) -> list[ComponentCandidate]:
        service_files: set[str] = set()
        evidence_list: list[SourceEvidence] = []
        service_class_names: list[str] = []

        for f in codebase_ir.files:
            file_path_lower = f.file_path.replace("\\", "/").lower()
            path_parts = set(file_path_lower.split("/"))
            filename = file_path_lower.split("/")[-1].replace(".py", "")

            # 1. File in services/ directory
            in_service_dir = bool(path_parts & self._SERVICE_DIR_KEYWORDS)

            # 2. File name matches service pattern
            is_service_file = any(filename.endswith(suf) for suf in self._SERVICE_CLASS_SUFFIXES)

            # 3. Classes with service-pattern names
            for cls in f.classes:
                cls_lower = cls.name.lower()
                # Skip pydantic models and test classes
                if "BaseModel" in cls.bases or cls.name.startswith("Test"):
                    continue
                is_service_class = any(cls_lower.endswith(suf) for suf in self._SERVICE_CLASS_SUFFIXES)
                has_enough_methods = len(cls.methods) >= self._MIN_METHODS_FOR_SERVICE

                if is_service_class and has_enough_methods:
                    service_files.add(f.file_path)
                    service_class_names.append(cls.name)
                    evidence_list.append(
                        SourceEvidence(
                            evidence_type=EvidenceType.FILE_CONTENT,
                            source_path=f.file_path,
                            snippet=f"class {cls.name} — service layer with {len(cls.methods)} methods",
                            confidence=0.8,
                            notes=f"Service class at {f.file_path}:{cls.line}",
                        )
                    )

            if (in_service_dir or is_service_file) and f.file_path.endswith(".py"):
                service_files.add(f.file_path)
                if in_service_dir:
                    evidence_list.append(
                        SourceEvidence(
                            evidence_type=EvidenceType.DIRECTORY_STRUCTURE,
                            source_path=f.file_path,
                            snippet=f"File in services/ directory: {f.file_path}",
                            confidence=0.7,
                            notes="Service layer by directory convention",
                        )
                    )

        if not service_files:
            return []

        return [ComponentCandidate(
            name="Service Layer",
            type=ComponentType.SERVICE_LAYER,
            technology="Python",
            evidence=evidence_list[:12],
            files=sorted(list(service_files)),
            score_evidence={
                "imports": 0.5,
                "code_behavior": min(1.0, 0.5 + 0.05 * len(evidence_list)),
                "config": 0.5,
                "framework_convention": 0.8 if service_class_names else 0.5,
                "docs": 0.0,
            },
            metadata={
                "service_classes": service_class_names[:10],
                "file_count": len(service_files),
            },
        )]
