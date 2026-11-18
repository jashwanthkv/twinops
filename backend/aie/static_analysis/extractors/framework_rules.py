"""Engine for detecting framework-specific patterns across an entire CodebaseIR."""

from __future__ import annotations

from collections import Counter
from aie.static_analysis.ir import CodebaseIR


class FrameworkRuleEngine:
    """Infers web framework, database access libraries, and caching technologies

    from parsed CodebaseIR framework patterns and imports.
    """

    _WEB_FRAMEWORKS = {
        "fastapi": ["fastapi"],
        "flask": ["flask"],
        "express": ["express"],
        "django": ["django"],
        "spring": ["org.springframework", "springframework"],
        "nestjs": ["@nestjs"],
        "tornado": ["tornado"],
        "aiohttp": ["aiohttp"],
    }

    _DB_LIBRARIES = {
        "sqlalchemy": ["sqlalchemy"],
        "mongoose": ["mongoose"],
        "prisma": ["@prisma", "prisma"],
        "sequelize": ["sequelize"],
        "typeorm": ["typeorm"],
        "hibernate": ["org.hibernate", "hibernate"],
        "asyncpg": ["asyncpg"],
        "psycopg2": ["psycopg2"],
        "pymongo": ["pymongo"],
        "motor": ["motor"],
        "tortoise": ["tortoise"],
        "peewee": ["peewee"],
        "pg": ["pg"],
    }

    _CACHE_LIBRARIES = {
        "redis": ["redis", "aioredis", "ioredis"],
        "memcached": ["memcached", "pylibmc", "pymemcache"],
        "cachetools": ["cachetools"],
    }

    def detect_web_framework(self, codebase_ir: CodebaseIR) -> str | None:
        """Detect the primary web framework used across the codebase."""
        scores: Counter[str] = Counter()

        # 1. Pattern occurrences (explicit routes detected)
        for f in codebase_ir.files:
            for p in f.framework_patterns:
                if p.type == "route":
                    scores[p.framework.lower()] += 5

            # 2. Imports
            for imp in f.imports:
                mod = (imp.module or imp.from_module or "").lower()
                for fw, markers in self._WEB_FRAMEWORKS.items():
                    if any(m in mod for m in markers):
                        scores[fw] += 1

        if not scores:
            return None

        # Return the framework with the highest confidence / occurrence count
        return scores.most_common(1)[0][0]

    def detect_db_access_patterns(self, codebase_ir: CodebaseIR) -> list[str]:
        """Detect database access technologies/ORMs in use."""
        detected: set[str] = set()

        for f in codebase_ir.files:
            for p in f.framework_patterns:
                if p.type in {"orm", "database"}:
                    detected.add(p.framework.lower())

            for imp in f.imports:
                mod = (imp.module or imp.from_module or "").lower()
                for db_name, markers in self._DB_LIBRARIES.items():
                    if any(m in mod for m in markers):
                        detected.add(db_name)

        return sorted(list(detected))

    def detect_cache_patterns(self, codebase_ir: CodebaseIR) -> list[str]:
        """Detect caching technologies in use."""
        detected: set[str] = set()

        for f in codebase_ir.files:
            for p in f.framework_patterns:
                if p.type == "cache":
                    detected.add(p.framework.lower())

            for imp in f.imports:
                mod = (imp.module or imp.from_module or "").lower()
                for cache_name, markers in self._CACHE_LIBRARIES.items():
                    if any(m in mod for m in markers):
                        detected.add(cache_name)

        return sorted(list(detected))
