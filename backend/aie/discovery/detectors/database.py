"""Database detector — detects databases from imports, connection strings, configs."""
from __future__ import annotations
from pathlib import Path
import re
import logging

logger = logging.getLogger(__name__)

DB_PATTERNS = {
    "PostgreSQL": [
        r"psycopg2", r"asyncpg", r"pg\.Pool", r"postgresql://", r"postgres://",
        r"POSTGRES", r"DATABASE_URL.*postgres",
    ],
    "MySQL": [
        r"pymysql", r"mysql\.connector", r"aiomysql", r"mysql://",
        r"MYSQL_", r"DATABASE_URL.*mysql",
    ],
    "MongoDB": [
        r"pymongo", r"motor\.", r"MongoClient", r"mongodb://",
        r"MONGO_", r"mongoose",
    ],
    "Redis": [
        r"import redis", r"from redis", r"aioredis", r"redis\.Redis",
        r"REDIS_URL", r"redis://",
    ],
    "SQLite": [
        r"sqlite3", r"sqlite:///", r"DATABASE_URL.*sqlite",
    ],
    "Elasticsearch": [
        r"elasticsearch", r"Elasticsearch\(", r"es\.index",
    ],
}


class DatabaseDetector:
    def detect(self, workspace_root: Path, file_inventory: list) -> dict:
        result: dict = {"databases": [], "connection_strings": []}
        found: set[str] = set()

        source_files = [
            entry for entry in file_inventory
            if hasattr(entry, "category") and str(entry.category) in
            ("FileCategory.SOURCE_CODE", "source_code", "FileCategory.CONFIGURATION", "configuration")
        ]

        for entry in source_files:
            rel = entry.file_path if hasattr(entry, "file_path") else str(entry)
            path = workspace_root / rel
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue

            for db_name, patterns in DB_PATTERNS.items():
                if db_name in found:
                    continue
                for pat in patterns:
                    if re.search(pat, text, re.IGNORECASE):
                        found.add(db_name)
                        result["databases"].append({
                            "name": db_name,
                            "detected_from": rel,
                        })
                        break

        return result
