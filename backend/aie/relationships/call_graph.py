"""Call graph relationship builder.

Analyzes function and method call patterns between components in the codebase
to identify direct invocations and architectural calling relationships.
"""

from __future__ import annotations

import os
from typing import Any

from aie.models import (
    ComponentType,
    DiscoveredComponent,
    DiscoveredRelationship,
    EvidenceType,
    RelationType,
    RelationshipConfidence,
    SourceEvidence,
)
from aie.static_analysis.ir import CallInfo, CodebaseIR, FileIR


class CallRelationshipBuilder:
    """Discovers relationships based on direct invocations and call patterns."""

    def build(
        self,
        components: list[DiscoveredComponent],
        codebase_ir: CodebaseIR | Any,
    ) -> list[DiscoveredRelationship]:
        """Find call relationships between components.

        Direct calls between known functions/classes yield CONFIRMED confidence.
        Pattern-based calling conventions (e.g., service -> repository, web -> service)
        yield INFERRED confidence.
        """
        if not components or not codebase_ir:
            return []

        files: list[FileIR] = getattr(codebase_ir, "files", [])
        if not files:
            return []

        run_id = components[0].run_id if components else ""

        # Map files to components
        file_to_component: dict[str, DiscoveredComponent] = {}
        for comp in components:
            for ev in comp.evidence:
                if ev.source_path:
                    file_to_component[self._normalize_path(ev.source_path)] = comp
            meta_files = comp.metadata.get("files", [])
            if isinstance(meta_files, list):
                for mf in meta_files:
                    if isinstance(mf, str):
                        file_to_component[self._normalize_path(mf)] = comp
            for rp in comp.root_paths:
                file_to_component[self._normalize_path(rp)] = comp

        # Index classes and functions to their containing components
        class_to_comp: dict[str, DiscoveredComponent] = {}
        func_to_comp: dict[str, DiscoveredComponent] = {}

        for file_ir in files:
            file_norm = self._normalize_path(file_ir.file_path)
            comp = self._find_component_for_file(file_norm, file_to_component, components)
            if not comp:
                continue

            for cls in file_ir.classes:
                class_to_comp[cls.name] = comp
                # Also index qualified name if available
                class_to_comp[cls.name.lower()] = comp

            for fn in file_ir.functions:
                func_to_comp[fn.name] = comp
                func_to_comp[fn.name.lower()] = comp

        relationships: list[DiscoveredRelationship] = []
        seen_pairs: set[tuple[str, str, str]] = set()

        # Traverse all calls
        for file_ir in files:
            source_file_norm = self._normalize_path(file_ir.file_path)
            source_comp = self._find_component_for_file(
                source_file_norm, file_to_component, components
            )
            if not source_comp:
                continue

            all_calls: list[CallInfo] = list(file_ir.global_calls)
            for fn in file_ir.functions:
                all_calls.extend(fn.calls)

            for call in all_calls:
                target_comp, confidence, reason = self._resolve_call(
                    call,
                    source_comp,
                    class_to_comp,
                    func_to_comp,
                    components,
                )

                if (
                    target_comp
                    and target_comp.component_id != source_comp.component_id
                ):
                    pair_key = (
                        source_comp.component_id,
                        target_comp.component_id,
                        RelationType.CALLS.value,
                    )
                    if pair_key in seen_pairs:
                        continue
                    seen_pairs.add(pair_key)

                    rel = DiscoveredRelationship(
                        run_id=run_id,
                        source_component_id=source_comp.component_id,
                        target_component_id=target_comp.component_id,
                        relation_type=RelationType.CALLS,
                        confidence=confidence,
                        description=(
                            f"{source_comp.name} calls {target_comp.name} "
                            f"({reason}: '{call.callee}' at line {call.line})"
                        ),
                        evidence=[
                            SourceEvidence(
                                evidence_type=EvidenceType.FILE_CONTENT,
                                source_path=call.file or file_ir.file_path,
                                snippet=f"line {call.line}: call to '{call.callee}'",
                                confidence=1.0 if confidence == RelationshipConfidence.CONFIRMED else 0.8,
                                notes=reason,
                            )
                        ],
                        metadata={
                            "callee": call.callee,
                            "line": call.line,
                            "source_file": call.file or file_ir.file_path,
                            "call_reason": reason,
                        },
                    )
                    relationships.append(rel)

        return relationships

    def _normalize_path(self, path: str) -> str:
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
        if file_path_norm in file_to_comp:
            return file_to_comp[file_path_norm]

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

        parts = file_path_norm.lower().split("/")
        for comp in components:
            cname = comp.name.lower().replace("-", "_").replace(" ", "_")
            if cname in parts:
                return comp

        return None

    def _resolve_call(
        self,
        call: CallInfo,
        source_comp: DiscoveredComponent,
        class_to_comp: dict[str, DiscoveredComponent],
        func_to_comp: dict[str, DiscoveredComponent],
        components: list[DiscoveredComponent],
    ) -> tuple[DiscoveredComponent | None, RelationshipConfidence, str]:
        """Resolve a CallInfo to a target component with confidence tier."""
        callee = call.callee.strip()
        if not callee:
            return None, RelationshipConfidence.INFERRED, ""

        callee_base = callee.split("(")[0].split(".")[-1]
        callee_full = callee.split("(")[0]

        # 1. Direct class instantiation or method invocation on known class
        for part in [callee, callee_full, callee_base]:
            if part in class_to_comp:
                target = class_to_comp[part]
                if target.component_id != source_comp.component_id:
                    return target, RelationshipConfidence.CONFIRMED, "Direct class reference"

        # 2. Direct function call to known function
        for part in [callee, callee_full, callee_base]:
            if part in func_to_comp:
                target = func_to_comp[part]
                if target.component_id != source_comp.component_id:
                    return target, RelationshipConfidence.CONFIRMED, "Direct function call"

        # 3. Architectural call pattern: Service calling repository / DB
        callee_lower = callee.lower()
        if any(term in callee_lower for term in ["repo", "repository", "dao", "query", "session", "cursor"]):
            for comp in components:
                if comp.component_id == source_comp.component_id:
                    continue
                if comp.component_type in (
                    ComponentType.DATABASE,
                    ComponentType.SERVICE,
                ) or any("db" in t.lower() or "sql" in t.lower() for t in comp.technologies):
                    if "repo" in comp.name.lower() or "data" in comp.name.lower() or "db" in comp.name.lower():
                        return comp, RelationshipConfidence.INFERRED, "Service calling Repository pattern"

        # 4. Architectural call pattern: Web handler calling Service
        if any(term in callee_lower for term in ["service", "client", "manager", "handler"]):
            for comp in components:
                if comp.component_id == source_comp.component_id:
                    continue
                cname = comp.name.lower()
                for token in callee_lower.split("."):
                    if token in cname or cname in token:
                        return comp, RelationshipConfidence.INFERRED, "Web calling Service pattern"

        # 5. Component name match in callee
        for comp in components:
            if comp.component_id == source_comp.component_id:
                continue
            cname = comp.name.lower().replace("-", "_").replace(" ", "_")
            if cname in callee_lower:
                return comp, RelationshipConfidence.INFERRED, "Callee identifier matches component name"

        return None, RelationshipConfidence.INFERRED, ""
