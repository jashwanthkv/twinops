"""Config extractor: reads .env, .env.example, settings files.

Emits Tier A/B Evidence for environment variable keys.
"""
from __future__ import annotations

import re
from pathlib import Path

from aie.evidence.models import Evidence


_ENV_LINE_RE = re.compile(r"^([A-Z][A-Z0-9_]{2,})\s*=")
_SETTINGS_ASSIGN_RE = re.compile(r"([A-Z][A-Z0-9_]{2,})\s*=\s*os\.(?:environ|getenv)")


class ConfigExtractor:
    _ENV_FILENAMES = {
        ".env", ".env.example", ".env.sample", ".env.template",
        ".env.local", ".env.development", ".env.production",
    }

    def extract(self, repo_root: str | Path, file_paths: list[str]) -> list[Evidence]:
        repo_root = Path(repo_root)
        items: list[Evidence] = []
        for rel_path in file_paths:
            name = Path(rel_path).name.lower()
            abs_path = repo_root / rel_path
            try:
                content = abs_path.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue

            if name in self._ENV_FILENAMES:
                items.extend(self._parse_dotenv(rel_path, content))
            elif "settings" in name or "config" in name:
                items.extend(self._parse_settings(rel_path, content))
        return items

    def _parse_dotenv(self, file_path: str, content: str) -> list[Evidence]:
        items = []
        for i, line in enumerate(content.splitlines(), start=1):
            m = _ENV_LINE_RE.match(line.strip())
            if m:
                key = m.group(1)
                eid = Evidence.make_id("env_var", file_path, i, key)
                items.append(Evidence(
                    id=eid, kind="env_var", tier="A",
                    file=file_path, line=i, symbol=None, value=key.lower(), ecosystem="python",
                ))
        return items

    def _parse_settings(self, file_path: str, content: str) -> list[Evidence]:
        items = []
        for i, line in enumerate(content.splitlines(), start=1):
            m = _SETTINGS_ASSIGN_RE.search(line)
            if m:
                key = m.group(1)
                eid = Evidence.make_id("config_key", file_path, i, key)
                items.append(Evidence(
                    id=eid, kind="config_key", tier="B",
                    file=file_path, line=i, symbol=None, value=key.lower(), ecosystem="python",
                ))
        return items
