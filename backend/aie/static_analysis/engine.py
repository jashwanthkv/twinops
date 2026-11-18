"""Static analysis engine that parses repository files and aggregates CodebaseIR."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from aie.models import FileCategory, FileEntry, TechnologyProfile
from aie.static_analysis.ir import CodebaseIR, FileIR
from aie.static_analysis.parsers.js_parser import JSRegexParser
from aie.static_analysis.parsers.python_parser import PythonASTParser

logger = logging.getLogger(__name__)

MAX_FILE_SIZE = 500 * 1024  # 500 KB


class StaticAnalysisEngine:
    """Orchestrates static parsing across all source files in the repository."""

    def __init__(self) -> None:
        self.py_parser = PythonASTParser()
        self.js_parser = JSRegexParser()

    async def analyze(
        self,
        workspace: Any,
        inventory: Any,
        profile: TechnologyProfile | None = None,
    ) -> CodebaseIR:
        """Parse source files from inventory and return an aggregated CodebaseIR."""
        root_path = (
            Path(workspace.root_path)
            if hasattr(workspace, "root_path")
            else Path(str(workspace))
        )

        # Extract file list from inventory
        file_entries: list[Any] = []
        if hasattr(inventory, "files"):
            file_entries = inventory.files
        elif isinstance(inventory, list):
            file_entries = inventory
        elif isinstance(inventory, dict) and "files" in inventory:
            file_entries = inventory["files"]

        # Filter candidates for static code analysis
        source_files: list[tuple[Path, str]] = []
        for entry in file_entries:
            path_str = getattr(entry, "path", None)
            if not path_str and isinstance(entry, dict):
                path_str = entry.get("path")
            if not path_str:
                continue

            # Check binary flag
            is_bin = getattr(entry, "is_binary", False)
            if isinstance(entry, dict):
                is_bin = entry.get("is_binary", False)
            if is_bin:
                continue

            # Check size
            size = getattr(entry, "size_bytes", 0)
            if isinstance(entry, dict):
                size = entry.get("size_bytes", 0)
            if size > MAX_FILE_SIZE:
                continue

            # Check category if available
            cat = getattr(entry, "category", None)
            if isinstance(entry, dict):
                cat = entry.get("category")
            if cat is not None and cat not in {FileCategory.SOURCE_CODE, "source_code", None}:
                # If explicitly categorized as test, doc, asset etc, skip
                if cat in {FileCategory.ASSET, FileCategory.DATA, "asset", "data"}:
                    continue

            # Determine language
            lang = getattr(entry, "language", None)
            if isinstance(entry, dict):
                lang = entry.get("language")

            full_path = root_path / path_str
            source_files.append((full_path, lang or ""))

        # Parse files using asyncio.to_thread in parallel
        semaphore = asyncio.Semaphore(16)

        async def _parse_worker(target_path: Path, language: str) -> FileIR | None:
            async with semaphore:
                return await asyncio.to_thread(self._parse_single_file, target_path, language)

        tasks = [_parse_worker(p, lang) for p, lang in source_files]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        parsed_files: list[FileIR] = []
        total_classes = 0
        total_functions = 0
        total_imports = 0

        for r in results:
            if isinstance(r, FileIR):
                parsed_files.append(r)
                total_classes += len(r.classes)
                total_functions += len(r.functions)
                total_imports += len(r.imports)
            elif isinstance(r, Exception):
                logger.warning("Error during static parsing task: %s", r)

        logger.info(
            "Static analysis complete: %d files parsed (%d classes, %d functions, %d imports)",
            len(parsed_files),
            total_classes,
            total_functions,
            total_imports,
        )

        return CodebaseIR(
            files=parsed_files,
            total_classes=total_classes,
            total_functions=total_functions,
            total_imports=total_imports,
        )

    def _parse_single_file(self, file_path: Path, language: str) -> FileIR | None:
        """Synchronous parsing dispatch for a single file."""
        if not file_path.is_file():
            return None

        try:
            stat = file_path.stat()
            if stat.st_size > MAX_FILE_SIZE:
                return None
        except OSError:
            return None

        ext = file_path.suffix.lower()

        # Python
        if ext == ".py" or language.lower() == "python":
            return self.py_parser.parse_file(file_path)

        # JavaScript / TypeScript
        if ext in {".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"} or language.lower() in {
            "javascript",
            "typescript",
        }:
            return self.js_parser.parse_file(file_path)

        # Fallback for other source files: return minimal FileIR without crashing
        return FileIR(
            file_path=str(file_path),
            language=language or ext.lstrip("."),
            parse_error=False,
        )
