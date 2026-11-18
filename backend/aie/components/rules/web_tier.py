"""Rule for detecting web tier / API layer components."""

from __future__ import annotations

from typing import Any
from aie.components.scoring import ComponentCandidate
from aie.models import ComponentType, EvidenceType, SourceEvidence, TechnologyProfile
from aie.static_analysis.ir import CodebaseIR


class WebTierRule:
    """Detects HTTP web tier / API components from route definitions, decorators, and controllers."""

    _ROUTE_DECORATORS = {
        "get", "post", "put", "delete", "patch", "route", "api_view",
        "Controller", "RestController", "GetMapping", "PostMapping", "PutMapping", "DeleteMapping",
        "RequestMapping",
    }

    def detect(
        self,
        codebase_ir: CodebaseIR,
        profile: TechnologyProfile | None = None,
        docs_knowledge: Any = None,
    ) -> list[ComponentCandidate]:
        route_files: set[str] = set()
        evidence_list: list[SourceEvidence] = []
        technologies_detected: set[str] = set()
        has_imports = False

        # Scan files in CodebaseIR
        for f in codebase_ir.files:
            file_has_routes = False

            # 1. Framework patterns from static analysis
            for p in f.framework_patterns:
                if p.type == "route":
                    file_has_routes = True
                    technologies_detected.add(p.framework)
                    evidence_list.append(
                        SourceEvidence(
                            evidence_type=EvidenceType.FILE_CONTENT,
                            source_path=f.file_path,
                            snippet=f"{p.framework} route: {p.details.get('method', '')} {p.details.get('path', p.details.get('decorator', ''))}",
                            confidence=0.95,
                            notes=f"Detected route pattern in {f.file_path}:{p.line}",
                        )
                    )

            # 2. Check function decorators (e.g. @app.get, @Controller)
            for fn in f.functions:
                for dec in fn.decorators:
                    base_dec = dec.split(".")[-1]
                    if base_dec in self._ROUTE_DECORATORS:
                        file_has_routes = True
                        evidence_list.append(
                            SourceEvidence(
                                evidence_type=EvidenceType.FILE_CONTENT,
                                source_path=f.file_path,
                                snippet=f"@{dec} def {fn.name}(...)",
                                confidence=0.9,
                                notes=f"Route handler decorator in {f.file_path}:{fn.line}",
                            )
                        )

            # 3. Check classes for annotations/decorators
            for cls in f.classes:
                for dec in cls.decorators:
                    base_dec = dec.split(".")[-1]
                    if base_dec in {"Controller", "RestController"}:
                        file_has_routes = True
                        technologies_detected.add("Spring")
                        evidence_list.append(
                            SourceEvidence(
                                evidence_type=EvidenceType.FILE_CONTENT,
                                source_path=f.file_path,
                                snippet=f"@{dec} class {cls.name}",
                                confidence=0.95,
                                notes=f"Controller class in {f.file_path}:{cls.line}",
                            )
                        )

            # 4. Check imports
            for imp in f.imports:
                mod = (imp.module or imp.from_module or "").lower()
                if any(fw in mod for fw in ["fastapi", "flask", "express", "django", "spring"]):
                    has_imports = True

            if file_has_routes:
                route_files.add(f.file_path)

        if not route_files and not evidence_list:
            return []

        # Cross-reference with technology profile
        profile_boost = 0.0
        primary_tech = "Web API"
        if profile and hasattr(profile, "frameworks"):
            for fw in profile.frameworks:
                fw_name = getattr(fw, "name", "").lower()
                if any(w in fw_name for w in ["fastapi", "flask", "express", "django", "spring", "koa", "nest"]):
                    profile_boost = 0.2
                    technologies_detected.add(fw.name)
                    if primary_tech == "Web API":
                        primary_tech = fw.name

        if technologies_detected:
            primary_tech = sorted(list(technologies_detected))[0].capitalize()

        # Check documentation knowledge
        doc_score = 0.0
        if docs_knowledge:
            docs_text = str(docs_knowledge).lower()
            if any(term in docs_text for term in ["api", "rest", "endpoint", "route", "http", "controller"]):
                doc_score = 0.8

        # Multiple route files boost framework convention confidence
        num_files = len(route_files)
        convention_score = min(1.0, 0.6 + (0.1 * num_files) + profile_boost)
        code_behavior_score = min(1.0, 0.7 + (0.05 * len(evidence_list)))
        imports_score = 0.9 if has_imports else 0.5
        config_score = 0.7 if profile_boost > 0 else 0.4

        candidate = ComponentCandidate(
            name=f"{primary_tech} Web Service",
            type=ComponentType.WEB_TIER,
            technology=primary_tech,
            evidence=evidence_list[:15],
            files=sorted(list(route_files)),
            score_evidence={
                "imports": imports_score,
                "code_behavior": code_behavior_score,
                "config": config_score,
                "framework_convention": convention_score,
                "docs": doc_score,
            },
            metadata={
                "route_count": len(evidence_list),
                "route_files_count": num_files,
                "technologies": sorted(list(technologies_detected)),
            },
        )

        return [candidate]
