"""Candidate formation: builds ArchCandidate structs from evidence + CodebaseIR.

Steps:
  S1. Collect all imports across the codebase.
  S2. Build a file→imports adjacency for proximity scoring.
  S3. Detect cross-cutting layers: auth, rate-limiting, middleware, ORM/DB.
  S4. Detect technology infrastructure nodes from tech_matches.
  S5. Group remaining files by domain (using ORM entity proximity heuristic).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from aie.static_analysis.ir import CodebaseIR

try:
    from aie.evidence.models import Evidence, EvidenceBundle
    from aie.evidence.catalog_resolver import TechMatch
    _EVIDENCE_OK = True
except ImportError:
    _EVIDENCE_OK = False
    EvidenceBundle = object  # type: ignore
    TechMatch = object  # type: ignore


# ── Auth/security pattern signals ──────────────────────────────────────────
_AUTH_PACKAGES = {
    "python-jose", "jose", "pyjwt", "PyJWT", "authlib",
    "fastapi-users", "fastapi-jwt-auth", "passlib", "bcrypt", "argon2-cffi",
}
_AUTH_DECORATORS = {"require_auth", "login_required", "jwt_required", "oauth_required", "get_current_user"}
_AUTH_IMPORT_PATTERNS = {"jose", "jwt", "passlib", "bcrypt", "oauth", "auth"}

_RATE_LIMIT_PACKAGES = {"slowapi", "limits", "flask-limiter", "fastapi-limiter"}
_RATE_LIMIT_DECORATORS = {"limiter.limit", "rate_limit", "RateLimiter"}
_RATE_LIMIT_IMPORT_PATTERNS = {"slowapi", "limits", "ratelimit"}

_MIDDLEWARE_PATTERNS = {"add_middleware", "BaseHTTPMiddleware", "Middleware", "CORSMiddleware",
                        "TrustedHostMiddleware", "SessionMiddleware", "GZipMiddleware"}

_ORM_PATTERNS = {"Base", "DeclarativeBase", "SQLModel", "BaseModel"}
_ORM_IMPORTS = {"sqlalchemy", "sqlmodel", "tortoise", "peewee", "django.db"}

_BACKGROUND_PATTERNS = {"BackgroundTasks", "BackgroundTask", "@celery.task", "@app.task", "dramatiq"}
_BACKGROUND_IMPORTS = {"celery", "dramatiq", "rq", "arq", "huey"}


@dataclass
class ArchCandidate:
    """A candidate architectural component before LLM synthesis."""
    name: str                        # Working name (may be refined by LLM)
    kind: str                        # auth|rate_limiter|middleware|api_routing|domain_service|
                                     # background_worker|database|cache|vector_store|ai_agent|
                                     # messaging|observability|infra|unknown
    layer: str                       # edge|security|application|integration|data|messaging|ai|
                                     # observability|platform|presentation|other
    files: list[str] = field(default_factory=list)
    technologies: list[str] = field(default_factory=list)
    evidence_ids: list[str] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)
    confidence: float = 0.5


@dataclass
class CandidateFormationResult:
    candidates: list[ArchCandidate]
    tech_nodes: list[TechMatch]  # infrastructure technology nodes
    domain_files: list[str]       # files not assigned to any candidate


def form_candidates(
    codebase_ir: "CodebaseIR",
    bundle: "EvidenceBundle | None" = None,
    tech_matches: "list[TechMatch] | None" = None,
) -> CandidateFormationResult:
    """Entry point: derive ArchCandidates from evidence + IR."""
    if tech_matches is None:
        tech_matches = []
    if bundle is None or not _EVIDENCE_OK:
        # Fallback: return empty candidates, let old rules handle it
        return CandidateFormationResult(candidates=[], tech_nodes=tech_matches, domain_files=[])

    candidates: list[ArchCandidate] = []
    assigned_files: set[str] = set()

    # Collect all imports and class info per file
    all_imports_lower: dict[str, set[str]] = {}
    all_classes: dict[str, list[str]] = {}
    all_calls: dict[str, set[str]] = {}
    all_decorators: dict[str, set[str]] = {}

    for f in codebase_ir.files:
        fp = f.file_path
        imports_lower: set[str] = set()
        for imp in f.imports:
            mod = (imp.module or imp.from_module or "").lower()
            imports_lower.add(mod)
            imports_lower.add(mod.split(".")[0])
        all_imports_lower[fp] = imports_lower
        all_classes[fp] = [cls.name for cls in f.classes]

        calls: set[str] = set()
        decs: set[str] = set()
        for fn in f.functions:
            for call in fn.calls:
                calls.add(call.callee.split(".")[-1])
            for dec in getattr(fn, "decorators", []):
                decs.add(str(dec))
        all_calls[fp] = calls
        all_decorators[fp] = decs

    # S1 — Authentication layer
    auth_files: list[str] = []
    for fp, imports in all_imports_lower.items():
        if any(pat in imp for imp in imports for pat in _AUTH_IMPORT_PATTERNS):
            auth_files.append(fp)
        elif any(dec in all_decorators.get(fp, set()) for dec in _AUTH_DECORATORS):
            auth_files.append(fp)
    auth_tech = [m for m in tech_matches if m.tech_id in {"jwt", "oauth2", "bcrypt", "fastapi_auth"}]
    if auth_files or auth_tech:
        auth_ev = [e.id for e in bundle.items if e.kind == "dependency"
                   and any(p in e.value for p in {"jose", "jwt", "passlib", "bcrypt", "auth"})]
        candidates.append(ArchCandidate(
            name="Authentication",
            kind="auth",
            layer="security",
            files=list(set(auth_files)),
            technologies=[m.display_name for m in auth_tech],
            evidence_ids=auth_ev,
            confidence=0.85 if auth_ev else 0.5,
        ))
        assigned_files.update(auth_files)

    # S2 — Rate Limiting layer
    rate_files: list[str] = []
    for fp, imports in all_imports_lower.items():
        if any(pat in imp for imp in imports for pat in _RATE_LIMIT_IMPORT_PATTERNS):
            rate_files.append(fp)
        elif any(dec in all_calls.get(fp, set()) for dec in {"Limiter", "RateLimiter"}):
            rate_files.append(fp)
    rate_tech = [m for m in tech_matches if m.tech_id in {"slowapi", "limits"}]
    if rate_files or rate_tech:
        rate_ev = [e.id for e in bundle.items if e.kind == "dependency"
                   and any(p in e.value for p in {"slowapi", "limits", "limiter"})]
        candidates.append(ArchCandidate(
            name="Rate Limiting",
            kind="rate_limiter",
            layer="security",
            files=list(set(rate_files)),
            technologies=[m.display_name for m in rate_tech],
            evidence_ids=rate_ev,
            confidence=0.85 if rate_ev else 0.5,
        ))
        assigned_files.update(rate_files)

    # S3 — Middleware / CORS
    middleware_files: list[str] = []
    for fp, calls in all_calls.items():
        if calls & _MIDDLEWARE_PATTERNS:
            middleware_files.append(fp)
    for fp, classes in all_classes.items():
        if any(cls in _MIDDLEWARE_PATTERNS for cls in classes):
            middleware_files.append(fp)
    if middleware_files:
        candidates.append(ArchCandidate(
            name="Middleware Stack",
            kind="middleware",
            layer="application",
            files=list(set(middleware_files)),
            technologies=[],
            evidence_ids=[],
            confidence=0.65,
        ))
        assigned_files.update(middleware_files)

    # S4 — Background Workers
    worker_files: list[str] = []
    for fp, imports in all_imports_lower.items():
        if any(pat in imp for imp in imports for pat in _BACKGROUND_IMPORTS):
            worker_files.append(fp)
    for fp, classes in all_classes.items():
        if any(cls in _BACKGROUND_PATTERNS for cls in classes):
            worker_files.append(fp)
    if worker_files:
        worker_ev = [e.id for e in bundle.items if e.kind == "dependency"
                     and any(p in e.value for p in {"celery", "dramatiq", "rq", "arq", "huey"})]
        candidates.append(ArchCandidate(
            name="Background Workers",
            kind="background_worker",
            layer="application",
            files=list(set(worker_files)),
            technologies=[m.display_name for m in tech_matches if m.tech_id == "celery"],
            evidence_ids=worker_ev,
            confidence=0.75 if worker_ev else 0.5,
        ))
        assigned_files.update(worker_files)

    # S5 — API Routing (fastapi/flask route files)
    route_files: list[str] = []
    for fp, calls in all_calls.items():
        if any(r in calls for r in {"get", "post", "put", "delete", "patch", "include_router"}):
            path_parts = fp.replace("\\", "/").lower().split("/")
            if any(p in path_parts for p in {"routes", "routers", "views", "endpoints", "api"}):
                route_files.append(fp)
    if route_files:
        candidates.append(ArchCandidate(
            name="API Routing",
            kind="api_routing",
            layer="application",
            files=list(set(route_files)),
            technologies=[m.display_name for m in tech_matches
                          if m.tech_id in {"fastapi", "flask", "django"}],
            evidence_ids=[],
            confidence=0.8,
        ))
        assigned_files.update(route_files)

    # S6 — Infrastructure Data Stores & Edge from Tech Matches
    for tm in tech_matches:
        # Cache (e.g. Redis)
        if "cache" in tm.possible_roles:
            cache_files = [
                fp for fp, imports in all_imports_lower.items()
                if any(p in imp for imp in imports for p in {"redis", "aioredis"})
            ]
            candidates.append(ArchCandidate(
                name=f"{tm.display_name} Cache",
                kind="cache",
                layer=tm.layer or "data",
                files=cache_files,
                technologies=[tm.display_name],
                evidence_ids=tm.evidence_ids,
                confidence=tm.confidence or 0.85,
            ))
            assigned_files.update(cache_files)

        # Database (e.g. PostgreSQL, MySQL, MongoDB, SQLite)
        elif any(r in tm.possible_roles for r in {"primary_database", "document_store"}):
            db_files = [
                fp for fp, imports in all_imports_lower.items()
                if any(p in imp for imp in imports for p in {"psycopg", "asyncpg", "sqlalchemy", "pymongo", "motor"})
            ]
            candidates.append(ArchCandidate(
                name=f"{tm.display_name} Database",
                kind="database",
                layer=tm.layer or "data",
                files=db_files,
                technologies=[tm.display_name],
                evidence_ids=tm.evidence_ids,
                confidence=tm.confidence or 0.85,
            ))
            assigned_files.update(db_files)

        # Ingress / Reverse Proxy (e.g. NGINX, Traefik)
        elif any(r in tm.possible_roles for r in {"load_balancer", "reverse_proxy", "ingress"}):
            candidates.append(ArchCandidate(
                name=f"{tm.display_name} Ingress",
                kind="ingress",
                layer="edge",
                files=[],
                technologies=[tm.display_name],
                evidence_ids=tm.evidence_ids,
                confidence=tm.confidence or 0.8,
            ))

    # S7 — Domain Business Logic (unassigned services / models / logic)
    domain_files = [
        f.file_path for f in codebase_ir.files
        if f.file_path not in assigned_files
        and f.file_path.endswith(".py")
        and not f.file_path.endswith("__init__.py")
    ]
    if domain_files:
        candidates.append(ArchCandidate(
            name="Domain Business Logic",
            kind="domain_service",
            layer="application",
            files=domain_files,
            technologies=[],
            evidence_ids=[],
            confidence=0.7,
        ))

    return CandidateFormationResult(
        candidates=candidates,
        tech_nodes=tech_matches,
        domain_files=domain_files,
    )
