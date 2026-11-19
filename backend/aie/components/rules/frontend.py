"""Rule for detecting frontend/UI components (React, Vue, Angular, plain HTML)."""

from __future__ import annotations

from typing import Any
from aie.components.scoring import ComponentCandidate
from aie.models import ComponentType, EvidenceType, SourceEvidence, TechnologyProfile
from aie.static_analysis.ir import CodebaseIR


class FrontendRule:
    """Detects frontend UI components.

    Signals: package.json with react/vue/angular/next; presence of .jsx/.tsx/.vue files;
    directories named frontend/, ui/, client/, webapp/, src/ containing JS;
    index.html, App.jsx, App.tsx entry points.
    """

    _FRONTEND_FRAMEWORKS = {
        "react": "React",
        "vue": "Vue",
        "angular": "Angular",
        "next": "Next.js",
        "nuxt": "Nuxt.js",
        "svelte": "Svelte",
        "solid-js": "Solid",
        "gatsby": "Gatsby",
        "@angular/core": "Angular",
        "@vue/core": "Vue",
    }
    _FRONTEND_DIR_KEYWORDS = {"frontend", "ui", "client", "webapp", "web-app", "app", "src"}
    _FRONTEND_EXTENSIONS = {".jsx", ".tsx", ".vue", ".svelte"}
    _FRONTEND_FILES = {"app.jsx", "app.tsx", "index.jsx", "index.tsx", "app.js", "index.js"}

    def detect(
        self,
        codebase_ir: CodebaseIR,
        profile: TechnologyProfile | None = None,
        docs_knowledge: Any = None,
    ) -> list[ComponentCandidate]:
        frontend_files: set[str] = set()
        evidence_list: list[SourceEvidence] = []
        technologies: set[str] = set()
        has_js_framework = False

        for f in codebase_ir.files:
            file_path_norm = f.file_path.replace("\\", "/")
            file_lower = file_path_norm.lower()
            path_parts = set(file_lower.split("/"))
            filename = file_lower.split("/")[-1]
            ext = "." + filename.rsplit(".", 1)[-1] if "." in filename else ""

            # 1. JSX/TSX/Vue files are strong frontend signals
            if ext in self._FRONTEND_EXTENSIONS:
                has_js_framework = True
                frontend_files.add(f.file_path)
                evidence_list.append(
                    SourceEvidence(
                        evidence_type=EvidenceType.FILE_PATTERN,
                        source_path=f.file_path,
                        snippet=f"Frontend component file: {filename}",
                        confidence=0.9,
                        notes=f"UI component file detected: {f.file_path}",
                    )
                )

            # 2. Frontend entry point files
            if filename in self._FRONTEND_FILES and bool(path_parts & self._FRONTEND_DIR_KEYWORDS):
                has_js_framework = True
                frontend_files.add(f.file_path)

            # 3. Files in frontend/ or ui/ directories
            if path_parts & self._FRONTEND_DIR_KEYWORDS:
                frontend_files.add(f.file_path)

        # Cross-reference with technology profile for JS frameworks
        if profile and hasattr(profile, "frameworks"):
            for fw in profile.frameworks:
                fw_lower = fw.name.lower()
                for key, label in self._FRONTEND_FRAMEWORKS.items():
                    if key in fw_lower:
                        has_js_framework = True
                        technologies.add(label)
                        evidence_list.append(
                            SourceEvidence(
                                evidence_type=EvidenceType.DEPENDENCY_MANIFEST,
                                source_path="package.json",
                                snippet=f"Frontend framework: {fw.name} (confidence: {fw.confidence:.2f})",
                                confidence=fw.confidence,
                                notes="Detected from package.json dependencies",
                            )
                        )

        if not frontend_files and not has_js_framework:
            return []

        primary_tech = sorted(list(technologies))[0] if technologies else "React"

        return [ComponentCandidate(
            name=f"{primary_tech} Frontend",
            type=ComponentType.FRONTEND,
            technology=primary_tech,
            evidence=evidence_list[:10],
            files=sorted(list(frontend_files)),
            score_evidence={
                "imports": 0.5,
                "code_behavior": 0.7 if has_js_framework else 0.4,
                "config": 0.7 if technologies else 0.4,
                "framework_convention": 0.85 if frontend_files else 0.4,
                "docs": 0.0,
            },
            metadata={
                "technologies": sorted(list(technologies)),
                "frontend_file_count": len(frontend_files),
            },
        )]
