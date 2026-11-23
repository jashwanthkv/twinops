"""Infrastructure extractor: reads docker-compose, Dockerfile, nginx.conf.

Emits Tier A Evidence for services, images, ports.
"""
from __future__ import annotations

import re
from pathlib import Path

from aie.evidence.models import Evidence


class InfraExtractor:
    """Reads docker-compose.yml / docker-compose.yaml and Dockerfile."""

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

            if name in {"docker-compose.yml", "docker-compose.yaml",
                        "compose.yml", "compose.yaml"}:
                items.extend(self._parse_compose(rel_path, content))
            elif name == "dockerfile" or name.startswith("dockerfile."):
                items.extend(self._parse_dockerfile(rel_path, content))
            elif "nginx" in name and name.endswith(".conf"):
                items.extend(self._parse_nginx(rel_path, content))
        return items

    def _parse_compose(self, file_path: str, content: str) -> list[Evidence]:
        """Minimal YAML parse for docker-compose service images and ports."""
        items: list[Evidence] = []
        # Match: image: xxx  (simple string form)
        for i, line in enumerate(content.splitlines(), start=1):
            stripped = line.strip()
            m = re.match(r"image:\s*['\"]?([^\s'\"#]+)", stripped)
            if m:
                image = m.group(1).split(":")[0].lower()  # strip tag
                eid = Evidence.make_id("infra_service", file_path, i, image)
                items.append(Evidence(
                    id=eid, kind="infra_service", tier="A",
                    file=file_path, line=i, symbol=None, value=image, ecosystem="infra",
                ))
            # ports: "HOST:CONTAINER"
            pm = re.match(r'-\s+["\']?(\d+):(\d+)["\']?', stripped)
            if pm:
                port = pm.group(2)
                eid = Evidence.make_id("port", file_path, i, port)
                items.append(Evidence(
                    id=eid, kind="port", tier="A",
                    file=file_path, line=i, symbol=None, value=port, ecosystem="infra",
                ))
        return items

    def _parse_dockerfile(self, file_path: str, content: str) -> list[Evidence]:
        items: list[Evidence] = []
        for i, line in enumerate(content.splitlines(), start=1):
            stripped = line.strip().upper()
            if stripped.startswith("FROM "):
                image = line.strip().split()[1].split(":")[0].lower()
                eid = Evidence.make_id("infra_service", file_path, i, image)
                items.append(Evidence(
                    id=eid, kind="infra_service", tier="A",
                    file=file_path, line=i, symbol=None, value=image, ecosystem="infra",
                ))
            elif stripped.startswith("EXPOSE "):
                port = line.strip().split()[1]
                eid = Evidence.make_id("port", file_path, i, port)
                items.append(Evidence(
                    id=eid, kind="port", tier="A",
                    file=file_path, line=i, symbol=None, value=port, ecosystem="infra",
                ))
        return items

    def _parse_nginx(self, file_path: str, content: str) -> list[Evidence]:
        items: list[Evidence] = []
        for i, line in enumerate(content.splitlines(), start=1):
            m = re.search(r"proxy_pass\s+https?://([^\s;/]+)", line)
            if m:
                upstream = m.group(1)
                eid = Evidence.make_id("upstream", file_path, i, upstream)
                items.append(Evidence(
                    id=eid, kind="upstream", tier="A",
                    file=file_path, line=i, symbol=None, value=upstream, ecosystem="infra",
                ))
        return items
