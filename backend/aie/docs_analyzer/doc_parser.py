"""
DocParser — parses repository documentation (README, architecture docs) to extract architecture insights.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


class DocumentationKnowledge(BaseModel):
    architecture_mentions: list[str] = Field(default_factory=list)
    component_descriptions: dict[str, str] = Field(default_factory=dict)
    readme_summary: str | None = None
    api_endpoints_mentioned: list[str] = Field(default_factory=list)
    technologies_mentioned: list[str] = Field(default_factory=list)


class DocParser:
    """Parses markdown and text documentation across the repository."""

    KNOWN_TECH_KEYWORDS: tuple[str, ...] = (
        "FastAPI",
        "Flask",
        "Django",
        "Express",
        "NestJS",
        "Spring Boot",
        "Spring",
        "React",
        "Vue",
        "Next.js",
        "Nuxt",
        "Angular",
        "PostgreSQL",
        "Postgres",
        "MySQL",
        "MongoDB",
        "SQLite",
        "Redis",
        "Memcached",
        "Kafka",
        "RabbitMQ",
        "Celery",
        "Docker",
        "Kubernetes",
        "Terraform",
        "GraphQL",
        "gRPC",
        "WebSocket",
        "Elasticsearch",
    )

    API_ENDPOINT_PATTERN = re.compile(
        r"(?:GET|POST|PUT|DELETE|PATCH|OPTIONS)\s+([/][a-zA-Z0-9_\-/{}.]+)"
        r"|(?:\b(?:endpoint|route|path)\s*[:=]?\s*[`'\"]?)(/[a-zA-Z0-9_\-/{}.]+)[`'\"]?",
        re.IGNORECASE,
    )

    def parse(self, workspace: Any, inventory: Any) -> DocumentationKnowledge:
        workspace_root = self._get_workspace_root(workspace)
        all_paths = self._collect_doc_paths(workspace_root, inventory)

        readme_summary: str | None = None
        architecture_mentions: list[str] = []
        component_descriptions: dict[str, str] = {}
        api_endpoints: set[str] = set()
        tech_mentions: set[str] = set()

        # 1. Parse README
        readme_path = self._find_readme(workspace_root, all_paths)
        if readme_path:
            full_readme = workspace_root / readme_path if not Path(readme_path).is_absolute() else Path(readme_path)
            content = self._safe_read(full_readme)
            if content:
                readme_summary, sections = self._parse_markdown_sections(content)
                self._extract_keywords(content, tech_mentions, api_endpoints)
                for header, body in sections.items():
                    if any(term in header.lower() for term in ("architecture", "design", "overview", "system")):
                        architecture_mentions.append(f"[{header}] {body[:300]}")
                    if any(term in header.lower() for term in ("component", "service", "module", "api", "backend", "frontend", "worker")):
                        component_descriptions[header] = body[:400]

        # 2. Parse Architecture docs
        arch_docs = self._find_architecture_docs(workspace_root, all_paths)
        for rel_doc in arch_docs:
            full_doc = workspace_root / rel_doc if not Path(rel_doc).is_absolute() else Path(rel_doc)
            content = self._safe_read(full_doc)
            if content:
                _, sections = self._parse_markdown_sections(content)
                self._extract_keywords(content, tech_mentions, api_endpoints)
                architecture_mentions.append(f"Document {rel_doc}: {content[:500]}")
                for header, body in sections.items():
                    component_descriptions[f"{rel_doc}:{header}"] = body[:400]

        return DocumentationKnowledge(
            architecture_mentions=architecture_mentions,
            component_descriptions=component_descriptions,
            readme_summary=readme_summary,
            api_endpoints_mentioned=sorted(list(api_endpoints)),
            technologies_mentioned=sorted(list(tech_mentions)),
        )

    # ------------------------------------------------------------------
    # Markdown & Content extraction
    # ------------------------------------------------------------------

    def _parse_markdown_sections(self, content: str) -> tuple[str | None, dict[str, str]]:
        sections: dict[str, str] = {}
        lines = content.splitlines()

        current_heading: str | None = None
        current_lines: list[str] = []
        readme_summary: str | None = None

        heading_regex = re.compile(r"^(#{1,4})\s+(.+)$")

        for line in lines:
            h_match = heading_regex.match(line.strip())
            if h_match:
                if current_heading:
                    body = "\n".join(current_lines).strip()
                    sections[current_heading] = body
                    if not readme_summary and body:
                        readme_summary = body[:500]
                current_heading = h_match.group(2).strip()
                current_lines = []
            else:
                if current_heading:
                    current_lines.append(line)
                elif not readme_summary and line.strip() and not line.strip().startswith("#"):
                    # Pre-heading summary text
                    current_lines.append(line)

        if current_heading:
            body = "\n".join(current_lines).strip()
            sections[current_heading] = body
            if not readme_summary and body:
                readme_summary = body[:500]
        elif current_lines and not readme_summary:
            readme_summary = "\n".join(current_lines).strip()[:500]

        return readme_summary, sections

    def _extract_keywords(self, content: str, tech_mentions: set[str], api_endpoints: set[str]) -> None:
        content_lower = content.lower()
        for tech in self.KNOWN_TECH_KEYWORDS:
            if re.search(rf"\b{re.escape(tech.lower())}\b", content_lower):
                tech_mentions.add(tech)

        for match in self.API_ENDPOINT_PATTERN.finditer(content):
            ep = match.group(1) or match.group(2)
            if ep and ep.startswith("/") and not ep.endswith((".png", ".jpg", ".md", ".svg")):
                api_endpoints.add(ep)

    def _find_readme(self, workspace_root: Path, all_paths: list[str]) -> str | None:
        candidates = ["README.md", "README.rst", "readme.md", "README", "readme.txt"]
        for p in all_paths:
            if Path(p).name in candidates and "/" not in p.replace("\\", "/"):
                return p
        for cand in candidates:
            if (workspace_root / cand).exists():
                return cand
        return None

    def _find_architecture_docs(self, workspace_root: Path, all_paths: list[str]) -> list[str]:
        patterns = (
            "architecture.md",
            "architecture.rst",
            "docs/architecture.md",
            "docs/architecture.rst",
            "design.md",
            "docs/design.md",
            "docs/system.md",
            "docs/overview.md",
        )
        matched = []
        for p in all_paths:
            p_norm = p.replace("\\", "/").lower()
            if any(p_norm.endswith(pat.lower()) for pat in patterns):
                matched.append(p)
        return matched

    def _collect_doc_paths(self, workspace_root: Path, inventory: Any) -> list[str]:
        items = inventory
        if hasattr(inventory, "files"):
            items = inventory.files
        if isinstance(items, list):
            res = []
            for it in items:
                p = it.get("path") if isinstance(it, dict) else getattr(it, "path", None)
                if p:
                    res.append(p)
            return res
        return []

    def _safe_read(self, path: Path) -> str:
        try:
            return path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            return ""

    def _get_workspace_root(self, workspace: Any) -> Path:
        if isinstance(workspace, Path):
            return workspace
        if isinstance(workspace, str):
            return Path(workspace)
        if hasattr(workspace, "root_path"):
            return Path(workspace.root_path)
        return Path(".")
