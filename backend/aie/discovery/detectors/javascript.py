"""
JavaScriptDetector — detects JS/TS frameworks, databases from package.json.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class JavaScriptDetector:
    """Detect JavaScript / TypeScript technology from package.json."""

    FRAMEWORK_MAP: dict[str, str] = {
        "express": "Express",
        "fastify": "Fastify",
        "@nestjs/core": "NestJS",
        "nestjs": "NestJS",
        "react": "React",
        "react-dom": "React",
        "vue": "Vue",
        "@vue/core": "Vue",
        "next": "Next.js",
        "nuxt": "Nuxt.js",
        "koa": "Koa",
        "hapi": "Hapi",
        "@hapi/hapi": "Hapi",
        "sveltekit": "SvelteKit",
        "@sveltejs/kit": "SvelteKit",
        "svelte": "Svelte",
        "remix": "Remix",
        "@remix-run/node": "Remix",
        "astro": "Astro",
    }
    DATABASE_MAP: dict[str, str] = {
        "sequelize": "Sequelize",
        "typeorm": "TypeORM",
        "prisma": "Prisma",
        "@prisma/client": "Prisma",
        "mongoose": "MongoDB",
        "mongodb": "MongoDB",
        "pg": "PostgreSQL",
        "postgres": "PostgreSQL",
        "mysql": "MySQL",
        "mysql2": "MySQL",
        "knex": "Knex",
        "better-sqlite3": "SQLite",
        "sqlite3": "SQLite",
        "drizzle-orm": "Drizzle ORM",
    }
    CACHE_MAP: dict[str, str] = {
        "redis": "Redis",
        "ioredis": "Redis",
        "node-cache": "NodeCache",
        "lru-cache": "LRU Cache",
    }
    BROKER_MAP: dict[str, str] = {
        "bull": "Bull",
        "bullmq": "BullMQ",
        "amqplib": "RabbitMQ",
        "amqp-connection-manager": "RabbitMQ",
        "kafkajs": "Kafka",
        "kafka-node": "Kafka",
        "bee-queue": "BeeQueue",
    }
    WEB_SERVER_MAP: dict[str, str] = {
        "express": "Express",
        "http-server": "http-server",
        "serve": "serve",
    }

    DEP_MANAGER_FILES = {"package-lock.json": "npm", "yarn.lock": "Yarn", "pnpm-lock.yaml": "pnpm", "bun.lockb": "Bun"}

    ENTRY_POINT_MAP: dict[str, str] = {
        "index.js": "server",
        "index.ts": "server",
        "main.js": "main",
        "main.ts": "main",
        "server.js": "server",
        "server.ts": "server",
        "app.js": "server",
        "app.ts": "server",
        "worker.js": "worker",
        "worker.ts": "worker",
        "cli.js": "cli",
        "cli.ts": "cli",
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

        # Dep managers
        for item in file_inventory:
            fname = Path(item["path"]).name
            if fname in self.DEP_MANAGER_FILES:
                mgr = self.DEP_MANAGER_FILES[fname]
                if mgr not in seen["dependency_managers"]:
                    seen["dependency_managers"].add(mgr)
                    result["dependency_managers"].append({"name": mgr, "detected_from": item["path"], "category": "dependency_manager"})

        # Parse all package.json files (root first, then nested)
        pkg_files = [item for item in file_inventory if Path(item["path"]).name == "package.json"]
        all_deps: dict[str, str | None] = {}
        for item in pkg_files:
            all_deps.update(self._parse_package_json(workspace_root / item["path"] if not Path(item["path"]).is_absolute() else Path(item["path"])))

        # Classify
        for pkg_raw, version in all_deps.items():
            pkg = pkg_raw.lower()
            for key, tech in self.FRAMEWORK_MAP.items():
                if key == pkg:
                    if tech not in seen["frameworks"]:
                        seen["frameworks"].add(tech)
                        result["frameworks"].append({"name": tech, "version": version, "detected_from": pkg_raw, "confidence": 0.9})
                    break
            for key, tech in self.DATABASE_MAP.items():
                if key == pkg:
                    if tech not in seen["databases"]:
                        seen["databases"].add(tech)
                        result["databases"].append({"name": tech, "version": version, "detected_from": pkg_raw, "category": "database"})
                    break
            for key, tech in self.CACHE_MAP.items():
                if key == pkg:
                    if tech not in seen["caches"]:
                        seen["caches"].add(tech)
                        result["caches"].append({"name": tech, "version": version, "detected_from": pkg_raw, "category": "cache"})
                    break
            for key, tech in self.BROKER_MAP.items():
                if key == pkg:
                    if tech not in seen["message_brokers"]:
                        seen["message_brokers"].add(tech)
                        result["message_brokers"].append({"name": tech, "version": version, "detected_from": pkg_raw, "category": "message_broker"})
                    break

        # Entry points
        for item in file_inventory:
            fname = Path(item["path"]).name
            if fname in self.ENTRY_POINT_MAP:
                ep_type = self.ENTRY_POINT_MAP[fname]
                result["entry_points"].append({"file": item["path"], "type": ep_type})

        return result

    # ------------------------------------------------------------------
    def _parse_package_json(self, path: Path) -> dict[str, str | None]:
        deps: dict[str, str | None] = {}
        try:
            data = json.loads(path.read_text(encoding="utf-8", errors="ignore"))
            for section in ("dependencies", "devDependencies", "peerDependencies"):
                for name, ver in data.get(section, {}).items():
                    deps[name] = ver if isinstance(ver, str) else None
        except Exception:
            pass
        return deps
