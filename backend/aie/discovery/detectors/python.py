"""
PythonDetector — detects Python frameworks, databases, entry points.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any


class PythonDetector:
    """Detect Python-specific technology from requirements.txt / pyproject.toml."""

    # Map package name substrings → (category, tech_name, canonical_name)
    FRAMEWORK_MAP: dict[str, tuple[str, str]] = {
        "fastapi": ("frameworks", "FastAPI"),
        "flask": ("frameworks", "Flask"),
        "django": ("frameworks", "Django"),
        "starlette": ("frameworks", "Starlette"),
        "tornado": ("frameworks", "Tornado"),
        "aiohttp": ("frameworks", "aiohttp"),
        "sanic": ("frameworks", "Sanic"),
    }
    DATABASE_MAP: dict[str, tuple[str, str]] = {
        "sqlalchemy": ("databases", "SQLAlchemy"),
        "psycopg2": ("databases", "PostgreSQL"),
        "psycopg": ("databases", "PostgreSQL"),
        "asyncpg": ("databases", "PostgreSQL"),
        "pymysql": ("databases", "MySQL"),
        "aiomysql": ("databases", "MySQL"),
        "motor": ("databases", "MongoDB"),
        "pymongo": ("databases", "MongoDB"),
        "tortoise": ("databases", "Tortoise ORM"),
        "databases": ("databases", "databases"),
        "alembic": ("databases", "Alembic"),
        "peewee": ("databases", "Peewee"),
    }
    CACHE_MAP: dict[str, tuple[str, str]] = {
        "redis": ("caches", "Redis"),
        "aioredis": ("caches", "Redis"),
        "pylibmc": ("caches", "Memcached"),
        "pymemcache": ("caches", "Memcached"),
    }
    BROKER_MAP: dict[str, tuple[str, str]] = {
        "celery": ("message_brokers", "Celery"),
        "dramatiq": ("message_brokers", "Dramatiq"),
        "rq": ("message_brokers", "RQ"),
        "aio-pika": ("message_brokers", "RabbitMQ"),
        "pika": ("message_brokers", "RabbitMQ"),
        "aiokafka": ("message_brokers", "Kafka"),
        "kafka-python": ("message_brokers", "Kafka"),
    }
    DEP_MANAGER_FILES = {"requirements.txt", "pyproject.toml", "setup.cfg", "Pipfile", "poetry.lock"}

    ENTRY_POINT_MAP: dict[str, str] = {
        "main.py": "main",
        "app.py": "server",
        "run.py": "server",
        "manage.py": "cli",
        "wsgi.py": "server",
        "asgi.py": "server",
        "worker.py": "worker",
        "celery_worker.py": "worker",
        "server.py": "server",
        "cli.py": "cli",
    }

    def detect(self, workspace_root: Path, file_inventory: list[dict]) -> dict[str, Any]:
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

        all_files = {item["path"] for item in file_inventory}

        # Collect deps from requirements.txt
        packages = self._parse_requirements(workspace_root, all_files)
        packages.update(self._parse_pyproject(workspace_root, all_files))

        # Dep managers present
        for f in self.DEP_MANAGER_FILES:
            if f in all_files or any(p.endswith("/" + f) for p in all_files):
                name = "pip" if f == "requirements.txt" else "poetry" if "poetry" in f or "pyproject" in f else f
                if name not in seen["dependency_managers"]:
                    seen["dependency_managers"].add(name)
                    result["dependency_managers"].append({"name": name, "detected_from": f, "category": "dependency_manager"})

        # Classify packages
        for pkg, version in packages.items():
            pkg_lower = pkg.lower()
            matched = False
            for key, (cat, tech) in self.FRAMEWORK_MAP.items():
                if key in pkg_lower:
                    if tech not in seen[cat]:
                        seen[cat].add(tech)
                        result[cat].append({"name": tech, "version": version, "detected_from": pkg, "category": cat})
                    matched = True
                    break
            if not matched:
                for key, (cat, tech) in self.DATABASE_MAP.items():
                    if key in pkg_lower:
                        if tech not in seen[cat]:
                            seen[cat].add(tech)
                            result[cat].append({"name": tech, "version": version, "detected_from": pkg, "category": cat})
                        break
            if not matched:
                for key, (cat, tech) in self.CACHE_MAP.items():
                    if key in pkg_lower:
                        if tech not in seen[cat]:
                            seen[cat].add(tech)
                            result[cat].append({"name": tech, "version": version, "detected_from": pkg, "category": cat})
                        break
            if not matched:
                for key, (cat, tech) in self.BROKER_MAP.items():
                    if key in pkg_lower:
                        if tech not in seen[cat]:
                            seen[cat].add(tech)
                            result[cat].append({"name": tech, "version": version, "detected_from": pkg, "category": cat})
                        break

        # Entry points
        for item in file_inventory:
            fname = Path(item["path"]).name
            if fname in self.ENTRY_POINT_MAP:
                ep_type = self.ENTRY_POINT_MAP[fname]
                result["entry_points"].append({"file": item["path"], "type": ep_type})

        return result

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _parse_requirements(self, workspace_root: Path, all_files: set[str]) -> dict[str, str | None]:
        packages: dict[str, str | None] = {}
        req_candidates = ["requirements.txt", "requirements/base.txt", "requirements/prod.txt", "requirements/production.txt"]
        for candidate in req_candidates:
            path = workspace_root / candidate
            if not path.exists():
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
                for line in text.splitlines():
                    line = line.strip()
                    if not line or line.startswith("#") or line.startswith("-"):
                        continue
                    # Strip extras and markers: pkg[extra]>=1.0; python_version>="3.8"
                    line = re.split(r"[;#]", line)[0].strip()
                    match = re.match(r"^([A-Za-z0-9_.\-]+)(?:\[.*?\])?([><=!~^]+[\d.*]+)?", line)
                    if match:
                        name = match.group(1).lower().replace("_", "-")
                        version = match.group(2)
                        packages[name] = version
            except Exception:
                pass
        return packages

    def _parse_pyproject(self, workspace_root: Path, all_files: set[str]) -> dict[str, str | None]:
        packages: dict[str, str | None] = {}
        path = workspace_root / "pyproject.toml"
        if not path.exists():
            return packages
        try:
            import toml
            data = toml.loads(path.read_text(encoding="utf-8", errors="ignore"))
            # Poetry
            deps = data.get("tool", {}).get("poetry", {}).get("dependencies", {})
            for name, spec in deps.items():
                if name.lower() == "python":
                    continue
                ver = spec if isinstance(spec, str) else None
                packages[name.lower().replace("_", "-")] = ver
            # PEP 517 / setuptools
            for dep in data.get("project", {}).get("dependencies", []):
                match = re.match(r"^([A-Za-z0-9_.\-]+)", dep)
                if match:
                    packages[match.group(1).lower().replace("_", "-")] = None
        except Exception:
            pass
        return packages
