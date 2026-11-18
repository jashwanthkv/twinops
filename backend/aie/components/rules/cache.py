"""Rule for detecting caching components (requires verified usage, not just imports)."""

from __future__ import annotations

from typing import Any
from aie.components.scoring import ComponentCandidate
from aie.models import ComponentType, EvidenceType, SourceEvidence, TechnologyProfile
from aie.static_analysis.ir import CodebaseIR


class CacheRule:
    """Detects cache components (e.g. Redis, Memcached).

    Requires ACTUAL USAGE (get/set/etc. calls, decorators) rather than passive imports alone.
    """

    _CACHE_CALL_METHODS = {
        "get", "set", "setex", "hget", "hset", "hgetall", "delete", "expire",
        "incr", "decr", "mget", "mset", "zadd", "zrange", "lpush", "rpop",
        "pipeline", "execute", "publish",
    }

    _CACHE_DECORATORS = {"cache", "cached", "Cacheable", "cache_page", "memoize"}

    def detect(
        self,
        codebase_ir: CodebaseIR,
        profile: TechnologyProfile | None = None,
        docs_knowledge: Any = None,
    ) -> list[ComponentCandidate]:
        cache_files: set[str] = set()
        evidence_list: list[SourceEvidence] = []
        actual_calls_found = 0
        has_imports = False
        technologies_detected: set[str] = set()

        for f in codebase_ir.files:
            file_has_cache = False

            # 1. Framework patterns (e.g. Redis(), redis.from_url, aioredis)
            for p in f.framework_patterns:
                if p.type == "cache":
                    file_has_cache = True
                    actual_calls_found += 1
                    technologies_detected.add(p.framework.capitalize())
                    evidence_list.append(
                        SourceEvidence(
                            evidence_type=EvidenceType.FILE_CONTENT,
                            source_path=f.file_path,
                            snippet=f"{p.framework} constructor/client: {p.details.get('call', '')}",
                            confidence=0.9,
                            notes=f"Cache initialization pattern at {f.file_path}:{p.line}",
                        )
                    )

            # 2. Check function decorators (e.g. @Cacheable, @cached)
            for fn in f.functions:
                for dec in fn.decorators:
                    base_dec = dec.split(".")[-1]
                    if base_dec in self._CACHE_DECORATORS:
                        file_has_cache = True
                        actual_calls_found += 1
                        evidence_list.append(
                            SourceEvidence(
                                evidence_type=EvidenceType.FILE_CONTENT,
                                source_path=f.file_path,
                                snippet=f"@{dec} def {fn.name}(...)",
                                confidence=0.85,
                                notes=f"Cache decorator on function at {f.file_path}:{fn.line}",
                            )
                        )

            # 3. Check calls for actual cache get/set operations
            all_calls = list(f.global_calls)
            for fn in f.functions:
                all_calls.extend(fn.calls)

            for call in all_calls:
                parts = call.callee.split(".")
                if len(parts) >= 2:
                    obj_name = parts[0].lower()
                    method_name = parts[-1]
                    # Check if object looks like cache/redis and method is an actual cache operation
                    is_cache_obj = any(
                        prefix in obj_name
                        for prefix in ["redis", "cache", "client", "r", "red"]
                    )
                    if is_cache_obj and method_name in self._CACHE_CALL_METHODS:
                        file_has_cache = True
                        actual_calls_found += 1
                        technologies_detected.add("Redis")
                        evidence_list.append(
                            SourceEvidence(
                                evidence_type=EvidenceType.FILE_CONTENT,
                                source_path=f.file_path,
                                snippet=f"Call: {call.callee}(...)",
                                confidence=0.95,
                                notes=f"Direct cache operation call at {f.file_path}:{call.line}",
                            )
                        )

            # 4. Check imports
            for imp in f.imports:
                mod = (imp.module or imp.from_module or "").lower()
                if any(c in mod for c in ["redis", "aioredis", "ioredis", "memcached"]):
                    has_imports = True
                    if "redis" in mod:
                        technologies_detected.add("Redis")
                    elif "memcached" in mod:
                        technologies_detected.add("Memcached")

            if file_has_cache:
                cache_files.add(f.file_path)

        # STRICT REQUIREMENT: Must find ACTUAL USAGE not just import
        if actual_calls_found == 0:
            return []

        primary_tech = "Redis" if "Redis" in technologies_detected else (
            sorted(list(technologies_detected))[0] if technologies_detected else "Cache"
        )

        doc_score = 0.0
        if docs_knowledge:
            docs_text = str(docs_knowledge).lower()
            if any(term in docs_text for term in ["redis", "cache", "caching", "memcached", "key-value"]):
                doc_score = 0.85

        code_behavior_score = min(1.0, 0.6 + 0.05 * actual_calls_found)
        imports_score = 0.9 if has_imports else 0.4
        convention_score = 0.7 if len(cache_files) > 1 else 0.5
        config_score = 0.7

        candidate = ComponentCandidate(
            name=f"{primary_tech} Cache",
            type=ComponentType.CACHE,
            technology=primary_tech,
            evidence=evidence_list[:15],
            files=sorted(list(cache_files)),
            score_evidence={
                "imports": imports_score,
                "code_behavior": code_behavior_score,
                "config": config_score,
                "framework_convention": convention_score,
                "docs": doc_score,
            },
            metadata={
                "usage_calls_count": actual_calls_found,
                "files_count": len(cache_files),
                "technologies": sorted(list(technologies_detected)),
            },
        )

        return [candidate]
