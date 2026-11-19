"""Context builder for LLM component knowledge extraction."""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any

from aie.knowledge.schema import COMPONENT_ANALYSIS_SCHEMA
from aie.models import DiscoveredComponent
from aie.static_analysis.ir import CodebaseIR, FileIR

logger = logging.getLogger(__name__)

# Fallback secret regex patterns if secret_filter module is missing
_DEFAULT_SECRET_PATTERNS = [
    re.compile(r"(?i)(password|passwd|pwd|secret|token|api[_-]?key|auth[_-]?token|bearer|private[_-]?key)\s*[:=]\s*['\"]?([^'\"\s\r\n]+)['\"]?"),
    re.compile(r"(?i)bearer\s+[a-zA-Z0-9_\-\.]{20,}"),
    re.compile(r"(?i)(ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9_]{36}"),
    re.compile(r"-----BEGIN [A-Z ]+ PRIVATE KEY-----[\s\S]+?-----END [A-Z ]+ PRIVATE KEY-----"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
]


def _builtin_filter_text(text: str) -> str:
    """Built-in fallback secret filter."""
    result = text
    for pattern in _DEFAULT_SECRET_PATTERNS:
        result = pattern.sub(
            lambda m: f"{m.group(1)}: [REDACTED]" if m.lastindex and m.lastindex >= 2 else "[REDACTED]",
            result,
        )
    return result


try:
    from aie.docs_analyzer.secret_filter import SecretFilter
except ImportError:
    class SecretFilter:  # type: ignore[no-redef]
        """Fallback SecretFilter when docs_analyzer is not yet present."""

        @staticmethod
        def filter_text(text: str) -> str:
            return _builtin_filter_text(text)

        @staticmethod
        def filter_dict(d: dict[str, Any]) -> dict[str, Any]:
            cleaned = {}
            for k, v in d.items():
                if any(s in k.lower() for s in ["password", "secret", "token", "key", "auth"]):
                    cleaned[k] = "[REDACTED]"
                elif isinstance(v, str):
                    cleaned[k] = _builtin_filter_text(v)
                elif isinstance(v, dict):
                    cleaned[k] = SecretFilter.filter_dict(v)
                else:
                    cleaned[k] = v
            return cleaned

        @staticmethod
        def is_secret_key(key: str) -> bool:
            return any(s in key.lower() for s in ["password", "secret", "token", "key", "auth", "credential"])


class ContextBuilder:
    """Builds token-budgeted, secret-sanitized prompt contexts for LLM analysis."""

    MAX_TOKENS_PER_SECTION: int = 1500
    # Assuming ~4 characters per token
    MAX_CHARS_PER_SECTION: int = 1500 * 4  # 6000 chars

    def __init__(self, workspace_root: str | Path | None = None) -> None:
        self.workspace_root = Path(workspace_root) if workspace_root else None
        self.secret_filter = SecretFilter()

    def build(
        self,
        component: DiscoveredComponent,
        codebase_ir: CodebaseIR | Any,
        docs_knowledge: Any = None,
        config_findings: list[Any] | None = None,
        all_components: list[DiscoveredComponent] | None = None,
    ) -> dict[str, str]:
        """Construct prompt context dictionary for a specific component.

        Collects:
        - Source code from component files/evidence (budgeted and sanitized)
        - Configuration excerpts (strictly secret-filtered, no .env)
        - Documentation mentions
        - Related components summary
        - Target JSON schema
        """
        component_name = component.name
        component_type = component.component_type.value if hasattr(component.component_type, "value") else str(component.component_type)
        technologies = ", ".join(component.technologies) if component.technologies else (component.primary_language or "unknown")

        source_code_excerpts = self._collect_source_excerpts(component, codebase_ir)
        configuration_excerpts = self._collect_config_excerpts(component, config_findings)
        documentation_excerpts = self._collect_docs_excerpts(component, docs_knowledge)
        related_summary = self._collect_related_summary(component, all_components)

        schema_json = json.dumps(COMPONENT_ANALYSIS_SCHEMA, indent=2)

        return {
            "component_name": component_name,
            "component_type": component_type,
            "technology": technologies,
            "source_code_excerpts": source_code_excerpts or "(No source excerpts available)",
            "configuration_excerpts": configuration_excerpts or "(No configuration excerpts found)",
            "documentation_excerpts": documentation_excerpts or "(No documentation excerpts found)",
            "related_components_summary": related_summary or "(No related components listed)",
            "json_schema": schema_json,
        }

    def _collect_source_excerpts(
        self, component: DiscoveredComponent, codebase_ir: CodebaseIR | Any
    ) -> str:
        """Gather and truncate source code from component evidence files."""
        collected: list[str] = []
        seen_paths: set[str] = set()

        # Collect paths from evidence
        evidence_paths: list[str] = []
        for ev in component.evidence:
            if ev.source_path and ev.source_path not in seen_paths:
                evidence_paths.append(ev.source_path)
                seen_paths.add(ev.source_path)

        for rp in component.root_paths:
            if rp not in seen_paths:
                evidence_paths.append(rp)
                seen_paths.add(rp)

        # From codebase_ir, find FileIR matching these paths
        files: list[FileIR] = getattr(codebase_ir, "files", []) if codebase_ir else []
        ir_map = {f.file_path.replace("\\", "/").strip("/"): f for f in files}

        current_chars = 0
        for rel_path in evidence_paths:
            norm_path = rel_path.replace("\\", "/").strip("/")

            # SECURITY: Skip any secret file
            if self._is_secret_filename(norm_path):
                continue

            content_snippet = ""

            # Try reading from disk if workspace_root is provided
            if self.workspace_root:
                p_obj = Path(norm_path)
                if p_obj.is_absolute():
                    file_full = p_obj.resolve()
                else:
                    file_full = (self.workspace_root / norm_path).resolve()

                if file_full.is_file() and not self._is_secret_filename(file_full.name):
                    try:
                        raw_text = file_full.read_text(encoding="utf-8", errors="ignore")
                        content_snippet = raw_text[: self.MAX_CHARS_PER_SECTION // 2]
                    except Exception:
                        pass

            # If no disk content, build structural snippet from FileIR
            if not content_snippet and norm_path in ir_map:
                file_ir = ir_map[norm_path]
                lines = [f"# File: {norm_path}"]
                for imp in file_ir.imports[:10]:
                    lines.append(f"import {imp.module}")
                for cls in file_ir.classes[:5]:
                    lines.append(f"class {cls.name}({', '.join(cls.bases)}):")
                    for m in cls.methods[:5]:
                        lines.append(f"    def {m}(...): ...")
                for fn in file_ir.functions[:5]:
                    lines.append(f"def {fn.name}({', '.join(fn.params)}): ...")
                content_snippet = "\n".join(lines)

            if content_snippet:
                # Sanitize snippet
                sanitized = self.secret_filter.filter_text(content_snippet)
                snippet_block = f"--- File: {norm_path} ---\n{sanitized}\n"

                if current_chars + len(snippet_block) > self.MAX_CHARS_PER_SECTION:
                    remaining = self.MAX_CHARS_PER_SECTION - current_chars
                    if remaining > 100:
                        collected.append(snippet_block[:remaining] + "\n...[TRUNCATED]...")
                    break
                collected.append(snippet_block)
                current_chars += len(snippet_block)

        return "\n".join(collected)

    def _collect_config_excerpts(
        self, component: DiscoveredComponent, config_findings: list[Any] | None
    ) -> str:
        """Gather configuration findings relevant to this component, strictly redacting secrets."""
        if not config_findings:
            return ""

        collected: list[str] = []
        cname = component.name.lower()
        current_chars = 0

        for cf in config_findings:
            # Check if finding belongs or hints to this component
            hint = getattr(cf, "component_hint", "") or ""
            source = getattr(cf, "source_file", "") or ""
            key = getattr(cf, "key", "") or ""
            val = getattr(cf, "value", "") or ""
            line = getattr(cf, "line_number", None)

            # SECURITY: Absolutely reject .env files
            if ".env" in source.lower() and ".example" not in source.lower() and ".sample" not in source.lower():
                continue

            # Redact secrets
            if self.secret_filter.is_secret_key(str(key)):
                val = "[REDACTED]"
            else:
                val = self.secret_filter.filter_text(str(val))

            matches = (
                not hint
                or hint.lower() in cname
                or cname in hint.lower()
                or any(t.lower() in key.lower() for t in component.technologies)
            )

            if matches:
                line_str = f":line {line}" if line else ""
                entry = f"- Key: {key} = {val} (source: {source}{line_str})"
                if current_chars + len(entry) > self.MAX_CHARS_PER_SECTION:
                    break
                collected.append(entry)
                current_chars += len(entry) + 1

        return "\n".join(collected)

    def _collect_docs_excerpts(
        self, component: DiscoveredComponent, docs_knowledge: Any
    ) -> str:
        """Collect documentation excerpts mentioning this component."""
        if not docs_knowledge:
            return ""

        cname = component.name.lower()
        cname_space = cname.replace("_", " ").replace("-", " ")
        cname_clean = cname.replace("_", "").replace("-", "")

        def _name_matches(text: str) -> bool:
            t = text.lower()
            return cname in t or cname_space in t or cname_clean in t.replace(" ", "").replace("_", "").replace("-", "")

        mentions: list[str] = []

        # Check dictionary or object attributes
        if isinstance(docs_knowledge, dict):
            comp_descs = docs_knowledge.get("component_descriptions", {})
            if isinstance(comp_descs, dict):
                for k, v in comp_descs.items():
                    if _name_matches(k):
                        mentions.append(f"Doc Description ({k}): {v}")

            arch = docs_knowledge.get("architecture_mentions", [])
            if isinstance(arch, list):
                for item in arch:
                    if _name_matches(str(item)):
                        mentions.append(f"Architecture mention: {item}")

            summary = docs_knowledge.get("readme_summary", "")
            if summary and _name_matches(summary):
                mentions.append(f"README Summary excerpt: {summary[:500]}")
        else:
            comp_descs = getattr(docs_knowledge, "component_descriptions", {})
            if isinstance(comp_descs, dict):
                for k, v in comp_descs.items():
                    if _name_matches(k):
                        mentions.append(f"Doc Description ({k}): {v}")
            arch = getattr(docs_knowledge, "architecture_mentions", [])
            if isinstance(arch, list):
                for item in arch:
                    if _name_matches(str(item)):
                        mentions.append(f"Architecture mention: {item}")
            summary = getattr(docs_knowledge, "readme_summary", "")
            if summary and _name_matches(summary):
                mentions.append(f"README Summary excerpt: {summary[:500]}")

        full_text = "\n".join(mentions)
        full_text = self.secret_filter.filter_text(full_text)
        return full_text[: self.MAX_CHARS_PER_SECTION]

    def _collect_related_summary(
        self,
        component: DiscoveredComponent,
        all_components: list[DiscoveredComponent] | None,
    ) -> str:
        """Create a compact summary of peer components in the architecture."""
        if not all_components:
            return ""

        lines: list[str] = []
        for other in all_components:
            if other.component_id == component.component_id:
                continue
            otype = other.component_type.value if hasattr(other.component_type, "value") else str(other.component_type)
            techs = ", ".join(other.technologies[:3]) if other.technologies else "unknown"
            lines.append(f"- {other.name} ({otype}, techs: {techs})")

        return "\n".join(lines[:15])

    def _is_secret_filename(self, filename: str) -> bool:
        """Return True if filename or path matches secret file patterns."""
        lower = filename.lower().replace("\\", "/")
        base = os.path.basename(lower)

        if base in {".env", "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519"}:
            return True
        if base.startswith(".env") and not base.endswith(".example") and not base.endswith(".sample"):
            return True
        for suffix in [".pem", ".key", ".p12", ".pfx", ".cer", ".crt", ".keystore", ".jks"]:
            if base.endswith(suffix):
                return True
        return False
