"""
ConfigExtractor — extracts key configuration settings from config files while redacting secrets.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

from aie.docs_analyzer.secret_filter import SecretFilter


class ConfigFinding(BaseModel):
    key: str
    value: Any
    source_file: str
    line_number: int | None = None
    component_hint: str | None = None


class ConfigExtractor:
    """Extracts architectural configuration parameters from known configuration files."""

    INTERESTING_KEYS: tuple[str, ...] = (
        "DB_HOST",
        "DATABASE_URL",
        "DB_PORT",
        "DB_NAME",
        "DB_USER",
        "DATABASE_HOST",
        "POSTGRES_HOST",
        "POSTGRES_DB",
        "MYSQL_HOST",
        "MONGO_URI",
        "MONGODB_URI",
        "POOL_SIZE",
        "MAX_CONNECTIONS",
        "DB_POOL_SIZE",
        "SQLALCHEMY_POOL_SIZE",
        "REDIS_URL",
        "REDIS_HOST",
        "REDIS_PORT",
        "CACHE_URL",
        "TIMEOUT",
        "CONNECT_TIMEOUT",
        "READ_TIMEOUT",
        "REQUEST_TIMEOUT",
        "PORT",
        "APP_PORT",
        "SERVER_PORT",
        "HOST",
        "APP_HOST",
        "SERVER_HOST",
        "KAFKA_BOOTSTRAP_SERVERS",
        "RABBITMQ_URL",
        "AMQP_URL",
        "CELERY_BROKER_URL",
    )

    COMPONENT_HINTS: dict[str, str] = {
        "db": "database",
        "database": "database",
        "postgres": "database",
        "mysql": "database",
        "mongo": "database",
        "sql": "database",
        "pool": "database",
        "connection": "database",
        "redis": "cache",
        "cache": "cache",
        "memcached": "cache",
        "kafka": "message_broker",
        "rabbitmq": "message_broker",
        "amqp": "message_broker",
        "broker": "message_broker",
        "celery": "message_broker",
        "port": "server",
        "server": "server",
        "host": "server",
    }

    def __init__(self) -> None:
        self.secret_filter = SecretFilter()

    def extract(self, workspace: Any, inventory: Any) -> list[ConfigFinding]:
        """
        Extracts configuration settings from workspace.
        Never reads .env files (only .env.example, .env.sample, etc.).
        """
        workspace_root = self._get_workspace_root(workspace)
        findings: list[ConfigFinding] = []

        files_to_check = self._find_config_files(workspace_root, inventory)

        for rel_path in files_to_check:
            # Strictly skip any actual .env file
            fname = Path(rel_path).name.lower()
            if fname == ".env" or fname.startswith(".env.local") or fname.startswith(".env.production"):
                continue

            full_path = workspace_root / rel_path if not Path(rel_path).is_absolute() else Path(rel_path)
            if not full_path.exists():
                continue

            if fname in (".env.example", ".env.sample", ".env.template", ".env.dist"):
                findings.extend(self._parse_env_file(full_path, rel_path))
            elif fname in ("application.properties", "application.ini"):
                findings.extend(self._parse_properties_file(full_path, rel_path))
            elif fname in ("application.yml", "application.yaml", "config.yml", "config.yaml"):
                findings.extend(self._parse_yaml_file(full_path, rel_path))
            elif fname == "settings.py":
                findings.extend(self._parse_settings_py(full_path, rel_path))

        return findings

    # ------------------------------------------------------------------
    # Parsing methods
    # ------------------------------------------------------------------

    def _parse_env_file(self, full_path: Path, rel_path: str) -> list[ConfigFinding]:
        results = []
        try:
            content = full_path.read_text(encoding="utf-8", errors="ignore")
            for idx, line in enumerate(content.splitlines(), start=1):
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    key, val = line.split("=", 1)
                    key = key.strip()
                    val = val.strip().strip("'\"")
                    if self._is_relevant_key(key):
                        safe_val = self._sanitize_value(key, val)
                        results.append(
                            ConfigFinding(
                                key=key,
                                value=safe_val,
                                source_file=rel_path,
                                line_number=idx,
                                component_hint=self._infer_component_hint(key),
                            )
                        )
        except Exception:
            pass
        return results

    def _parse_properties_file(self, full_path: Path, rel_path: str) -> list[ConfigFinding]:
        results = []
        try:
            content = full_path.read_text(encoding="utf-8", errors="ignore")
            for idx, line in enumerate(content.splitlines(), start=1):
                line = line.strip()
                if not line or line.startswith("#") or line.startswith("!"):
                    continue
                delimiter = "=" if "=" in line else ":" if ":" in line else None
                if delimiter:
                    key, val = line.split(delimiter, 1)
                    key = key.strip()
                    val = val.strip()
                    if self._is_relevant_key(key):
                        safe_val = self._sanitize_value(key, val)
                        results.append(
                            ConfigFinding(
                                key=key,
                                value=safe_val,
                                source_file=rel_path,
                                line_number=idx,
                                component_hint=self._infer_component_hint(key),
                            )
                        )
        except Exception:
            pass
        return results

    def _parse_yaml_file(self, full_path: Path, rel_path: str) -> list[ConfigFinding]:
        results = []
        try:
            content = full_path.read_text(encoding="utf-8", errors="ignore")
            data = yaml.safe_load(content)
            if isinstance(data, dict):
                self._flatten_yaml(data, "", rel_path, results)
        except Exception:
            pass
        return results

    def _flatten_yaml(self, d: dict[str, Any], prefix: str, rel_path: str, results: list[ConfigFinding]) -> None:
        for k, v in d.items():
            full_key = f"{prefix}.{k}" if prefix else str(k)
            if isinstance(v, dict):
                self._flatten_yaml(v, full_key, rel_path, results)
            else:
                if self._is_relevant_key(full_key):
                    safe_val = self._sanitize_value(full_key, v)
                    results.append(
                        ConfigFinding(
                            key=full_key,
                            value=safe_val,
                            source_file=rel_path,
                            line_number=None,
                            component_hint=self._infer_component_hint(full_key),
                        )
                    )

    def _parse_settings_py(self, full_path: Path, rel_path: str) -> list[ConfigFinding]:
        results = []
        try:
            content = full_path.read_text(encoding="utf-8", errors="ignore")
            # Safe regex extraction of uppercase assignments: KEY = "value" or KEY = 123
            pattern = re.compile(r"^([A-Z0-9_]{3,50})\s*=\s*(.+)$", re.MULTILINE)
            for idx, line in enumerate(content.splitlines(), start=1):
                match = pattern.match(line.strip())
                if match:
                    key = match.group(1).strip()
                    raw_val = match.group(2).strip().strip("'\"")
                    if self._is_relevant_key(key):
                        safe_val = self._sanitize_value(key, raw_val)
                        results.append(
                            ConfigFinding(
                                key=key,
                                value=safe_val,
                                source_file=rel_path,
                                line_number=idx,
                                component_hint=self._infer_component_hint(key),
                            )
                        )
        except Exception:
            pass
        return results

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _is_relevant_key(self, key: str) -> bool:
        key_upper = key.upper()
        # Direct match or partial match
        if any(target in key_upper for target in self.INTERESTING_KEYS):
            return True
        for hint in self.COMPONENT_HINTS:
            if hint.upper() in key_upper:
                return True
        return False

    def _sanitize_value(self, key: str, val: Any) -> Any:
        if self.secret_filter.is_secret_key(key):
            return "[REDACTED]"
        if isinstance(val, str):
            return self.secret_filter.filter_text(val)
        return val

    def _infer_component_hint(self, key: str) -> str:
        key_lower = key.lower()
        for k, hint in self.COMPONENT_HINTS.items():
            if k in key_lower:
                return hint
        return "general"

    def _find_config_files(self, workspace_root: Path, inventory: Any) -> list[str]:
        target_names = {
            ".env.example",
            ".env.sample",
            ".env.template",
            ".env.dist",
            "application.properties",
            "application.ini",
            "application.yml",
            "application.yaml",
            "config.yml",
            "config.yaml",
            "settings.py",
        }
        found: list[str] = []

        # From inventory if available
        items = inventory
        if hasattr(inventory, "files"):
            items = inventory.files
        if isinstance(items, list):
            for item in items:
                p = item.get("path") if isinstance(item, dict) else getattr(item, "path", None)
                if p and Path(p).name.lower() in target_names:
                    found.append(p)

        # Fallback to direct path check if inventory yielded few
        if not found:
            for name in target_names:
                p = workspace_root / name
                if p.exists():
                    found.append(name)

        return found

    def _get_workspace_root(self, workspace: Any) -> Path:
        if isinstance(workspace, Path):
            return workspace
        if isinstance(workspace, str):
            return Path(workspace)
        if hasattr(workspace, "root_path"):
            return Path(workspace.root_path)
        return Path(".")
