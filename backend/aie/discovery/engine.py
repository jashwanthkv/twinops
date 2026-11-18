"""
DiscoveryEngine — aggregates and coordinates all technology detectors.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

from aie.discovery.detectors.infrastructure import InfrastructureDetector
from aie.discovery.detectors.java import JavaDetector
from aie.discovery.detectors.javascript import JavaScriptDetector
from aie.discovery.detectors.python import PythonDetector
from aie.discovery.models import (
    EntryPoint,
    FrameworkInfo,
    LanguageInfo,
    TechnologyInfo,
    TechnologyProfile,
)


class DiscoveryEngine:
    """Discovers languages, frameworks, databases, and infrastructure across a repository."""

    def __init__(self) -> None:
        self.python_detector = PythonDetector()
        self.js_detector = JavaScriptDetector()
        self.java_detector = JavaDetector()
        self.infra_detector = InfrastructureDetector()

    def discover(self, workspace: Any, inventory: Any) -> TechnologyProfile:
        """
        Discovers technology stack from workspace root and inventory.
        
        Args:
            workspace: Path, str, or AnalysisWorkspace object containing root_path.
            inventory: list[dict], list[FileEntry], or object containing .files.
        """
        workspace_root = self._extract_workspace_root(workspace)
        normalized_inventory = self._normalize_inventory(inventory)

        # 1. Run all detectors
        py_res = self.python_detector.detect(workspace_root, normalized_inventory)
        js_res = self.js_detector.detect(workspace_root, normalized_inventory)
        java_res = self.java_detector.detect(workspace_root, normalized_inventory)
        infra_res = self.infra_detector.detect(workspace_root, normalized_inventory)

        # 2. Merge detector outputs
        merged_frameworks = self._merge_frameworks(
            py_res.get("frameworks", [])
            + js_res.get("frameworks", [])
            + java_res.get("frameworks", [])
            + infra_res.get("frameworks", [])
        )

        merged_databases = self._merge_technologies(
            py_res.get("databases", [])
            + js_res.get("databases", [])
            + java_res.get("databases", [])
            + infra_res.get("databases", []),
            default_category="database",
        )

        merged_caches = self._merge_technologies(
            py_res.get("caches", [])
            + js_res.get("caches", [])
            + java_res.get("caches", [])
            + infra_res.get("caches", []),
            default_category="cache",
        )

        merged_brokers = self._merge_technologies(
            py_res.get("message_brokers", [])
            + js_res.get("message_brokers", [])
            + java_res.get("message_brokers", [])
            + infra_res.get("message_brokers", []),
            default_category="message_broker",
        )

        merged_web_servers = self._merge_technologies(
            py_res.get("web_servers", [])
            + js_res.get("web_servers", [])
            + java_res.get("web_servers", [])
            + infra_res.get("web_servers", []),
            default_category="web_server",
        )

        merged_infra = self._merge_technologies(
            py_res.get("infrastructure", [])
            + js_res.get("infrastructure", [])
            + java_res.get("infrastructure", [])
            + infra_res.get("infrastructure", []),
            default_category="infrastructure",
        )

        merged_dep_managers = self._merge_technologies(
            py_res.get("dependency_managers", [])
            + js_res.get("dependency_managers", [])
            + java_res.get("dependency_managers", [])
            + infra_res.get("dependency_managers", []),
            default_category="dependency_manager",
        )

        merged_entry_points = self._merge_entry_points(
            py_res.get("entry_points", [])
            + js_res.get("entry_points", [])
            + java_res.get("entry_points", [])
            + infra_res.get("entry_points", [])
        )

        # 3. Compute language statistics
        languages, primary_language = self._compute_language_stats(normalized_inventory)

        # 4. Determine primary framework
        primary_framework = None
        if merged_frameworks:
            # Sort by confidence descending
            sorted_frameworks = sorted(merged_frameworks, key=lambda f: f.confidence, reverse=True)
            primary_framework = sorted_frameworks[0].name

        # 5. Populate unified technologies list
        all_technologies = (
            merged_databases
            + merged_caches
            + merged_brokers
            + merged_web_servers
            + merged_infra
            + merged_dep_managers
        )

        run_id = getattr(workspace, "workspace_id", None) or getattr(workspace, "run_id", None)

        return TechnologyProfile(
            run_id=str(run_id) if run_id else None,
            languages=languages,
            frameworks=merged_frameworks,
            databases=merged_databases,
            caches=merged_caches,
            message_brokers=merged_brokers,
            web_servers=merged_web_servers,
            infrastructure=merged_infra,
            dependency_managers=merged_dep_managers,
            entry_points=merged_entry_points,
            primary_language=primary_language,
            primary_framework=primary_framework,
            technologies=all_technologies,
        )

    # ------------------------------------------------------------------
    # Helper methods
    # ------------------------------------------------------------------

    def _extract_workspace_root(self, workspace: Any) -> Path:
        if isinstance(workspace, Path):
            return workspace
        if isinstance(workspace, str):
            return Path(workspace)
        if hasattr(workspace, "root_path"):
            return Path(workspace.root_path)
        return Path(".")

    def _normalize_inventory(self, inventory: Any) -> list[dict]:
        items = inventory
        if hasattr(inventory, "files"):
            items = inventory.files
        if not isinstance(items, list):
            items = list(items) if items is not None else []

        normalized: list[dict] = []
        for item in items:
            if isinstance(item, dict):
                normalized.append(item)
            elif hasattr(item, "model_dump"):
                normalized.append(item.model_dump())
            elif hasattr(item, "__dict__"):
                normalized.append(vars(item))
        return normalized

    def _compute_language_stats(self, inventory: list[dict]) -> tuple[list[LanguageInfo], str | None]:
        lang_counts: Counter[str] = Counter()
        for item in inventory:
            lang = item.get("language")
            if lang and lang.lower() not in ("unknown", "none"):
                lang_counts[lang] += 1

        total_files = sum(lang_counts.values())
        if total_files == 0:
            return [], None

        languages: list[LanguageInfo] = []
        primary_language = None
        max_count = -1

        for lang, count in lang_counts.most_common():
            pct = round((count / total_files) * 100.0, 2)
            languages.append(
                LanguageInfo(
                    name=lang,
                    file_count=count,
                    percentage=pct,
                )
            )
            if count > max_count:
                max_count = count
                primary_language = lang

        return languages, primary_language

    def _merge_frameworks(self, raw_list: list[dict]) -> list[FrameworkInfo]:
        seen: dict[str, FrameworkInfo] = {}
        for item in raw_list:
            name = item.get("name")
            if not name:
                continue
            ver = item.get("version")
            evidence_file = item.get("evidence_file") or item.get("detected_from")
            conf = float(item.get("confidence", 1.0))

            if name not in seen:
                seen[name] = FrameworkInfo(
                    name=name,
                    version=ver,
                    evidence_file=evidence_file,
                    confidence=conf,
                )
            else:
                if ver and not seen[name].version:
                    seen[name].version = ver
                if conf > seen[name].confidence:
                    seen[name].confidence = conf
        return list(seen.values())

    def _merge_technologies(self, raw_list: list[dict], default_category: str) -> list[TechnologyInfo]:
        seen: dict[str, TechnologyInfo] = {}
        for item in raw_list:
            name = item.get("name")
            if not name:
                continue
            ver = item.get("version")
            detected_from = item.get("detected_from")
            cat = item.get("category") or default_category

            if name not in seen:
                seen[name] = TechnologyInfo(
                    name=name,
                    version=ver,
                    detected_from=detected_from,
                    category=cat,
                )
            else:
                if ver and not seen[name].version:
                    seen[name].version = ver
        return list(seen.values())

    def _merge_entry_points(self, raw_list: list[dict]) -> list[EntryPoint]:
        seen: set[str] = set()
        entry_points: list[EntryPoint] = []
        for item in raw_list:
            file_path = item.get("file")
            ep_type = item.get("type", "main")
            if not file_path:
                continue
            key = f"{file_path}:{ep_type}"
            if key not in seen:
                seen.add(key)
                entry_points.append(EntryPoint(file=file_path, type=ep_type))
        return entry_points
