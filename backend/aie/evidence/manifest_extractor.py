"""Manifest extractor: reads requirements.txt, pyproject.toml, package.json.

Emits Tier A Evidence items for every declared package dependency.
"""
from __future__ import annotations

import re
from pathlib import Path

from aie.evidence.models import Evidence


_COMMENT_RE = re.compile(r"\s*#.*$")
_VERSION_RE = re.compile(r"[>=<!~^].*$")


def extract_from_requirements(file_path: str, content: str) -> list[Evidence]:
    """Parse requirements.txt format."""
    items: list[Evidence] = []
    for i, raw_line in enumerate(content.splitlines(), start=1):
        line = _COMMENT_RE.sub("", raw_line).strip()
        if not line or line.startswith("-"):
            continue
        pkg = _VERSION_RE.sub("", line).strip().lower().replace("_", "-")
        if pkg:
            eid = Evidence.make_id("dependency", file_path, i, pkg)
            items.append(Evidence(
                id=eid, kind="dependency", tier="A",
                file=file_path, line=i, symbol=None, value=pkg, ecosystem="python",
            ))
    return items


def extract_from_pyproject(file_path: str, content: str) -> list[Evidence]:
    """Very lightweight pyproject.toml dep extraction (no TOML parser required)."""
    items: list[Evidence] = []
    in_deps = False
    for i, raw_line in enumerate(content.splitlines(), start=1):
        stripped = raw_line.strip()
        if re.match(r'\[tool\.poetry\.dependencies\]|\[project\]', stripped):
            in_deps = True
        elif stripped.startswith("[") and in_deps:
            in_deps = False
        if in_deps:
            m = re.match(r'^([A-Za-z][A-Za-z0-9._-]+)\s*[=\^~><!]', stripped)
            if m:
                pkg = m.group(1).lower().replace("_", "-")
                eid = Evidence.make_id("dependency", file_path, i, pkg)
                items.append(Evidence(
                    id=eid, kind="dependency", tier="A",
                    file=file_path, line=i, symbol=None, value=pkg, ecosystem="python",
                ))
    return items


def extract_from_package_json(file_path: str, content: str) -> list[Evidence]:
    """Extract package names from package.json dependencies."""
    import json
    items: list[Evidence] = []
    try:
        data = json.loads(content)
    except Exception:
        return items
    for section in ("dependencies", "devDependencies", "peerDependencies"):
        for pkg in data.get(section, {}):
            eid = Evidence.make_id("dependency", file_path, None, pkg)
            items.append(Evidence(
                id=eid, kind="dependency", tier="A",
                file=file_path, line=None, symbol=None, value=pkg.lower(), ecosystem="node",
            ))
    return items


class ManifestExtractor:
    """Reads all dependency manifest files from a CodebaseIR file list."""

    _HANDLERS = {
        "requirements.txt": extract_from_requirements,
        "requirements-dev.txt": extract_from_requirements,
        "requirements_dev.txt": extract_from_requirements,
        "requirements-prod.txt": extract_from_requirements,
        "pyproject.toml": extract_from_pyproject,
        "package.json": extract_from_package_json,
    }

    def extract(self, repo_root: str | Path, file_paths: list[str]) -> list[Evidence]:
        repo_root = Path(repo_root)
        all_evidence: list[Evidence] = []
        for rel_path in file_paths:
            filename = Path(rel_path).name.lower()
            handler = self._HANDLERS.get(filename)
            if handler is None:
                continue
            abs_path = repo_root / rel_path
            try:
                content = abs_path.read_text(encoding="utf-8", errors="replace")
                all_evidence.extend(handler(rel_path, content))
            except Exception:
                pass
        return all_evidence
