"""Extractor for building import dependency graphs from CodebaseIR."""

from __future__ import annotations

import os
from pathlib import PurePath, PurePosixPath

from aie.static_analysis.ir import CodebaseIR, FileIR


def _normalize_path(p: str) -> str:
    """Normalize file path to POSIX style for uniform comparison."""
    return p.replace("\\", "/").rstrip("/")


class ImportGraphBuilder:
    """Constructs import dependency graphs from CodebaseIR."""

    def build(self, codebase_ir: CodebaseIR) -> dict[str, list[str]]:
        """Map each file path to a deduplicated list of all imported module names/paths."""
        graph: dict[str, list[str]] = {}

        for file_ir in codebase_ir.files:
            imported_modules: list[str] = []
            seen: set[str] = set()

            for imp in file_ir.imports:
                mod = imp.module
                if not mod and imp.from_module:
                    mod = imp.from_module
                if mod and mod not in seen:
                    seen.add(mod)
                    imported_modules.append(mod)

            graph[file_ir.file_path] = imported_modules

        return graph

    def get_internal_imports(self, codebase_ir: CodebaseIR) -> dict[str, list[str]]:
        """Identify which internal project files are imported by each file.

        Returns a dictionary mapping:
            file_path -> list of internal file_paths that this file imports.
        """
        all_file_paths = [_normalize_path(f.file_path) for f in codebase_ir.files]
        # Fast lookup set
        all_files_set = set(all_file_paths)

        # Build lookup table for basenames / stems and module equivalents
        # e.g., "foo/bar/baz.py" -> stem "baz", module path "foo.bar.baz", etc.
        stem_map: dict[str, list[str]] = {}
        for path_str in all_file_paths:
            p = PurePosixPath(path_str)
            stem = p.stem.lower()
            stem_map.setdefault(stem, []).append(path_str)

        internal_graph: dict[str, list[str]] = {}

        for file_ir in codebase_ir.files:
            curr_posix = _normalize_path(file_ir.file_path)
            curr_pure = PurePosixPath(curr_posix)
            curr_dir = curr_pure.parent

            internal_targets: list[str] = []
            seen_targets: set[str] = set()

            for imp in file_ir.imports:
                mod = imp.module or imp.from_module or ""
                if not mod:
                    continue

                # 1. Check relative JS/TS or Python imports: ./x, ../x, or is_relative
                if imp.is_relative or mod.startswith("."):
                    # Resolve relative path against curr_dir
                    # In python: mod could be .utils or ..helpers
                    # In js: ./foo, ../utils/bar
                    clean_mod = mod.lstrip(".")
                    levels_up = len(mod) - len(clean_mod)
                    target_dir = curr_dir
                    if levels_up > 1:
                        for _ in range(levels_up - 1):
                            target_dir = target_dir.parent

                    rel_subpath = clean_mod.replace(".", "/")
                    resolved_cand = (target_dir / rel_subpath).as_posix()

                    matched = self._find_matching_file(resolved_cand, all_files_set)
                    if matched and matched != curr_posix and matched not in seen_targets:
                        seen_targets.add(matched)
                        internal_targets.append(matched)
                        continue

                # 2. Check direct path match with any known repo file
                # Replace '.' with '/' for python packages: foo.bar -> foo/bar
                mod_as_path = mod.replace(".", "/")
                matched = self._find_matching_file(mod_as_path, all_files_set)
                if matched and matched != curr_posix and matched not in seen_targets:
                    seen_targets.add(matched)
                    internal_targets.append(matched)
                    continue

                # 3. Check stem match (e.g. from models import User -> models.py)
                first_part = mod.split(".")[0].lower()
                if first_part in stem_map:
                    for cand in stem_map[first_part]:
                        if cand != curr_posix and cand not in seen_targets:
                            # Verify if cand is in the same directory or child/parent
                            cand_pure = PurePosixPath(cand)
                            if cand_pure.parent == curr_dir or str(cand_pure).endswith(f"/{mod_as_path}.py") or str(cand_pure).endswith(f"/{first_part}.py") or str(cand_pure).endswith(f"/{first_part}.ts") or str(cand_pure).endswith(f"/{first_part}.js"):
                                seen_targets.add(cand)
                                internal_targets.append(cand)

            internal_graph[file_ir.file_path] = internal_targets

        return internal_graph

    def _find_matching_file(self, cand_path: str, all_files_set: set[str]) -> str | None:
        """Find matching file in all_files_set considering file extensions."""
        cand_clean = cand_path.rstrip("/")
        # Exact match
        if cand_clean in all_files_set:
            return cand_clean

        # Extensions to probe
        extensions = [".py", ".ts", ".js", ".tsx", ".jsx", "/__init__.py", "/index.ts", "/index.js"]
        for ext in extensions:
            probed = cand_clean + ext
            if probed in all_files_set:
                return probed
            # Also check if any file in all_files_set ends with probed
            for full_file in all_files_set:
                if full_file.endswith(probed) or full_file.endswith("/" + probed.lstrip("/")):
                    return full_file

        return None
