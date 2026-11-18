"""Import relationship builder.

Analyzes import dependencies between files across components in the codebase
to discover component-level dependency relationships.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from aie.models import (
    DiscoveredComponent,
    DiscoveredRelationship,
    EvidenceType,
    RelationType,
    RelationshipConfidence,
    SourceEvidence,
)
from aie.static_analysis.ir import CodebaseIR, FileIR, ImportInfo


class ImportRelationshipBuilder:
    """Builds component relationships based on file and module import statements."""

    def build(
        self,
        components: list[DiscoveredComponent],
        codebase_ir: CodebaseIR | Any,
    ) -> list[DiscoveredRelationship]:
        """Find relationships where files in component X import modules in component Y.

        If fileA imports fileB and fileA belongs to component X and fileB belongs
        to component Y (where X != Y), then component X depends_on component Y.
        Sets confidence = RelationshipConfidence.CONFIRMED.
        """
        if not components or not codebase_ir:
            return []

        files: list[FileIR] = getattr(codebase_ir, "files", [])
        if not files:
            return []

        run_id = components[0].run_id if components else ""

        # Map files and directory prefixes to components
        file_to_component: dict[str, DiscoveredComponent] = {}
        for comp in components:
            # Map explicit evidence paths
            for ev in comp.evidence:
                if ev.source_path:
                    norm = self._normalize_path(ev.source_path)
                    file_to_component[norm] = comp

            # Map paths in metadata files list
            meta_files = comp.metadata.get("files", [])
            if isinstance(meta_files, list):
                for mf in meta_files:
                    if isinstance(mf, str):
                        file_to_component[self._normalize_path(mf)] = comp

            # Map root paths
            for rp in comp.root_paths:
                file_to_component[self._normalize_path(rp)] = comp

        # Also build a fast index of known files in codebase
        known_files: dict[str, FileIR] = {
            self._normalize_path(f.file_path): f for f in files
        }

        relationships: list[DiscoveredRelationship] = []
        seen_pairs: set[tuple[str, str, str]] = set()

        for file_ir in files:
            source_file_norm = self._normalize_path(file_ir.file_path)
            source_comp = self._find_component_for_file(
                source_file_norm, file_to_component, components
            )
            if not source_comp:
                continue

            for imp in file_ir.imports:
                target_comp = self._resolve_import_to_component(
                    imp,
                    source_file_norm,
                    known_files,
                    file_to_component,
                    components,
                )

                if (
                    target_comp
                    and target_comp.component_id != source_comp.component_id
                ):
                    pair_key = (
                        source_comp.component_id,
                        target_comp.component_id,
                        RelationType.DEPENDS_ON.value,
                    )
                    if pair_key in seen_pairs:
                        continue
                    seen_pairs.add(pair_key)

                    import_str = imp.module
                    if imp.from_module:
                        import_str = f"from {imp.from_module} import {imp.name or imp.module}"

                    rel = DiscoveredRelationship(
                        run_id=run_id,
                        source_component_id=source_comp.component_id,
                        target_component_id=target_comp.component_id,
                        relation_type=RelationType.DEPENDS_ON,
                        confidence=RelationshipConfidence.CONFIRMED,
                        description=(
                            f"{source_comp.name} depends on {target_comp.name} "
                            f"(via import '{import_str}' in {file_ir.file_path})"
                        ),
                        evidence=[
                            SourceEvidence(
                                evidence_type=EvidenceType.FILE_CONTENT,
                                source_path=file_ir.file_path,
                                snippet=f"line {imp.line}: {import_str}",
                                confidence=1.0,
                                notes="Direct module import",
                            )
                        ],
                        metadata={
                            "import_module": imp.module,
                            "from_module": imp.from_module,
                            "imported_name": imp.name,
                            "line": imp.line,
                            "source_file": file_ir.file_path,
                        },
                    )
                    relationships.append(rel)

        return relationships

    def _normalize_path(self, path: str) -> str:
        """Normalize path separators to forward slashes, strip leading/trailing slashes."""
        p = path.replace("\\", "/").strip()
        if p.startswith("./"):
            p = p[2:]
        return p.strip("/")

    def _find_component_for_file(
        self,
        file_path_norm: str,
        file_to_comp: dict[str, DiscoveredComponent],
        components: list[DiscoveredComponent],
    ) -> DiscoveredComponent | None:
        """Find the component that owns the given file."""
        if file_path_norm in file_to_comp:
            return file_to_comp[file_path_norm]

        # Check directory prefix match
        best_match: DiscoveredComponent | None = None
        best_prefix_len = 0

        for comp in components:
            for rp in comp.root_paths:
                norm_rp = self._normalize_path(rp)
                if norm_rp and (
                    file_path_norm == norm_rp
                    or file_path_norm.startswith(norm_rp + "/")
                ):
                    if len(norm_rp) > best_prefix_len:
                        best_prefix_len = len(norm_rp)
                        best_match = comp

        if best_match:
            return best_match

        # Heuristic match: check if component name matches directory segment
        parts = file_path_norm.lower().split("/")
        for comp in components:
            comp_name_clean = comp.name.lower().replace("-", "_").replace(" ", "_")
            if comp_name_clean in parts:
                return comp

        return None

    def _resolve_import_to_component(
        self,
        imp: ImportInfo,
        source_file_norm: str,
        known_files: dict[str, FileIR],
        file_to_comp: dict[str, DiscoveredComponent],
        components: list[DiscoveredComponent],
    ) -> DiscoveredComponent | None:
        """Resolve an import statement to a target component."""
        # 1. Check relative imports (e.g. .service, ..db)
        if imp.is_relative or (imp.module and imp.module.startswith(".")):
            source_dir = os.path.dirname(source_file_norm).replace("\\", "/")
            module_path = imp.module.lstrip(".")
            candidate_base = (
                f"{source_dir}/{module_path}" if module_path else source_dir
            )
            candidate_base = os.path.normpath(candidate_base).replace("\\", "/")

            for ext in [".py", ".ts", ".js", ".jsx", ".tsx", "/__init__.py", "/index.ts", "/index.js"]:
                cand = self._normalize_path(candidate_base + ext)
                if cand in file_to_comp:
                    return file_to_comp[cand]
                if cand in known_files:
                    comp = self._find_component_for_file(cand, file_to_comp, components)
                    if comp:
                        return comp

        # 2. Check absolute/standard module imports (e.g., "app.services.user")
        module_path = imp.module.replace(".", "/")
        for ext in [".py", ".ts", ".js", ".jsx", ".tsx", "/__init__.py", "/index.ts", "/index.js", ""]:
            cand = self._normalize_path(module_path + ext)
            if cand in file_to_comp:
                return file_to_comp[cand]
            if cand in known_files:
                comp = self._find_component_for_file(cand, file_to_comp, components)
                if comp:
                    return comp

            # Also check matching end of known paths (e.g. src/app/services/user.py)
            for kpath in known_files:
                if kpath.endswith(cand) and cand:
                    comp = self._find_component_for_file(kpath, file_to_comp, components)
                    if comp:
                        return comp

        # 3. Check from_module if present
        if imp.from_module:
            from_path = imp.from_module.replace(".", "/")
            for ext in [".py", ".ts", ".js", "/__init__.py", ""]:
                cand = self._normalize_path(from_path + ext)
                if cand in file_to_comp:
                    return file_to_comp[cand]
                if cand in known_files:
                    comp = self._find_component_for_file(cand, file_to_comp, components)
                    if comp:
                        return comp

        # 4. Check against component names directly (e.g. "auth_service", "database", "redis")
        mod_parts = imp.module.lower().replace(".", "_").split("_")
        for comp in components:
            cname = comp.name.lower().replace("-", "_").replace(" ", "_")
            if cname in imp.module.lower() or cname in (imp.from_module or "").lower():
                return comp
            for tech in comp.technologies:
                if tech.lower() in imp.module.lower():
                    return comp

        return None
