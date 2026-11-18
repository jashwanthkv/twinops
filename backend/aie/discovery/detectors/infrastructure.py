"""
InfrastructureDetector — detects Docker, Compose, Kubernetes, Terraform, etc.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml


class InfrastructureDetector:
    """Detect infrastructure components from Dockerfiles, compose files, K8s manifests, Terraform."""

    SERVICE_IMAGE_MAP: dict[str, tuple[str, str]] = {
        "postgres": ("databases", "PostgreSQL"),
        "postgresql": ("databases", "PostgreSQL"),
        "mysql": ("databases", "MySQL"),
        "mariadb": ("databases", "MariaDB"),
        "mongo": ("databases", "MongoDB"),
        "mongodb": ("databases", "MongoDB"),
        "redis": ("caches", "Redis"),
        "memcached": ("caches", "Memcached"),
        "rabbitmq": ("message_brokers", "RabbitMQ"),
        "kafka": ("message_brokers", "Kafka"),
        "zookeeper": ("infrastructure", "ZooKeeper"),
        "elasticsearch": ("databases", "Elasticsearch"),
        "opensearch": ("databases", "OpenSearch"),
        "nginx": ("web_servers", "Nginx"),
        "caddy": ("web_servers", "Caddy"),
        "traefik": ("web_servers", "Traefik"),
        "apache": ("web_servers", "Apache HTTP Server"),
        "httpd": ("web_servers", "Apache HTTP Server"),
        "consul": ("infrastructure", "Consul"),
        "vault": ("infrastructure", "Vault"),
        "localstack": ("infrastructure", "LocalStack"),
    }

    def detect(self, workspace_root: Path, file_inventory: list[dict | Any]) -> dict[str, Any]:
        result: dict[str, Any] = {
            "frameworks": [],
            "databases": [],
            "caches": [],
            "message_brokers": [],
            "web_servers": [],
            "infrastructure": [],
            "dependency_managers": [],
            "entry_points": [],
        }
        seen: dict[str, set[str]] = {k: set() for k in result}

        # Normalize paths
        paths: list[str] = []
        for item in file_inventory:
            if isinstance(item, dict):
                p = item.get("path", "")
            else:
                p = getattr(item, "path", "")
            if p:
                paths.append(p.replace("\\", "/"))

        # 1. Dockerfile checks
        dockerfiles = [
            p for p in paths
            if Path(p).name.lower() == "dockerfile"
            or Path(p).name.lower().startswith("dockerfile.")
            or Path(p).name.lower().endswith(".dockerfile")
        ]
        if dockerfiles:
            if "Docker" not in seen["infrastructure"]:
                seen["infrastructure"].add("Docker")
                result["infrastructure"].append({
                    "name": "Docker",
                    "version": None,
                    "detected_from": dockerfiles[0],
                    "category": "infrastructure"
                })

        # 2. Docker Compose checks
        compose_candidates = [
            p for p in paths
            if Path(p).name.lower() in ("docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml")
            or "docker-compose" in Path(p).name.lower()
        ]
        if compose_candidates:
            if "Docker Compose" not in seen["infrastructure"]:
                seen["infrastructure"].add("Docker Compose")
                result["infrastructure"].append({
                    "name": "Docker Compose",
                    "version": None,
                    "detected_from": compose_candidates[0],
                    "category": "infrastructure"
                })
            for cp in compose_candidates:
                self._parse_compose(workspace_root, cp, result, seen)

        # 3. Kubernetes manifests
        k8s_files = [p for p in paths if p.endswith((".yml", ".yaml"))]
        for kp in k8s_files:
            if kp in compose_candidates:
                continue
            if self._is_kubernetes_manifest(workspace_root, kp):
                if "Kubernetes" not in seen["infrastructure"]:
                    seen["infrastructure"].add("Kubernetes")
                    result["infrastructure"].append({
                        "name": "Kubernetes",
                        "version": None,
                        "detected_from": kp,
                        "category": "infrastructure"
                    })
                break

        # 4. Terraform checks
        tf_files = [p for p in paths if p.endswith(".tf")]
        if tf_files:
            if "Terraform" not in seen["infrastructure"]:
                seen["infrastructure"].add("Terraform")
                result["infrastructure"].append({
                    "name": "Terraform",
                    "version": None,
                    "detected_from": tf_files[0],
                    "category": "infrastructure"
                })

        # 5. Helm checks
        helm_files = [p for p in paths if Path(p).name.lower() in ("chart.yaml", "chart.yml")]
        if helm_files:
            if "Helm" not in seen["infrastructure"]:
                seen["infrastructure"].add("Helm")
                result["infrastructure"].append({
                    "name": "Helm",
                    "version": None,
                    "detected_from": helm_files[0],
                    "category": "infrastructure"
                })

        return result

    # ------------------------------------------------------------------

    def _parse_compose(self, workspace_root: Path, rel_path: str, result: dict, seen: dict) -> None:
        full_path = workspace_root / rel_path if not Path(rel_path).is_absolute() else Path(rel_path)
        if not full_path.exists():
            return
        try:
            content = full_path.read_text(encoding="utf-8", errors="ignore")
            # Parse YAML safely
            data = yaml.safe_load(content)
            if not isinstance(data, dict):
                return
            services = data.get("services")
            if not isinstance(services, dict):
                return

            for svc_name, svc_conf in services.items():
                if not isinstance(svc_conf, dict):
                    continue
                image = str(svc_conf.get("image", "")).lower()
                svc_name_lower = str(svc_name).lower()
                target_str = f"{image} {svc_name_lower}"

                for key, (cat, tech_name) in self.SERVICE_IMAGE_MAP.items():
                    if key in target_str:
                        if tech_name not in seen[cat]:
                            seen[cat].add(tech_name)
                            # Extract version from image if present (e.g., redis:7.0-alpine -> 7.0-alpine)
                            ver = None
                            if ":" in image:
                                parts = image.split(":")
                                if len(parts) > 1 and not parts[1].startswith("/"):
                                    ver = parts[1]
                            result[cat].append({
                                "name": tech_name,
                                "version": ver,
                                "detected_from": f"{rel_path}:{svc_name}",
                                "category": cat
                            })
        except Exception:
            pass

    def _is_kubernetes_manifest(self, workspace_root: Path, rel_path: str) -> bool:
        full_path = workspace_root / rel_path if not Path(rel_path).is_absolute() else Path(rel_path)
        if not full_path.exists():
            return False
        try:
            # Read first few lines to check for apiVersion and kind
            with full_path.open("r", encoding="utf-8", errors="ignore") as f:
                head = "".join(f.readline() for _ in range(40))
            k8s_kinds = ["Deployment", "Service", "StatefulSet", "DaemonSet", "Ingress", "ConfigMap", "Pod", "Job", "CronJob"]
            has_apiversion = bool(re.search(r"^\s*apiVersion\s*:\s*[a-zA-Z0-9/.]+", head, re.MULTILINE))
            has_kind = any(re.search(rf"^\s*kind\s*:\s*{k}\b", head, re.MULTILINE) for k in k8s_kinds)
            return has_apiversion and has_kind
        except Exception:
            return False
