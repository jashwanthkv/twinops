"""Deterministic request flow builder.

Builds the request processing path deterministically from:
1. Infra edge services (from infra evidence): NGINX, Traefik, etc.
2. Middleware registrations (from CodebaseIR class/call analysis)
3. Auth/security Depends() chains (FastAPI dependency injection)
4. Route handlers (from route decorator analysis)
5. Data layer calls (ORM/cache calls within handlers)

The LLM never constructs the flow - it only labels step names.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from aie.static_analysis.ir import CodebaseIR

try:
    from aie.evidence.catalog_resolver import TechMatch
    _EV_OK = True
except ImportError:
    _EV_OK = False
    TechMatch = object  # type: ignore

logger = logging.getLogger(__name__)

_MIDDLEWARE_CLASSES = {
    "CORSMiddleware", "TrustedHostMiddleware", "SessionMiddleware",
    "GZipMiddleware", "BaseHTTPMiddleware", "HTTPSRedirectMiddleware",
    "ProxyHeadersMiddleware", "AuthenticationMiddleware",
}

_AUTH_DEPENDS = {
    "get_current_user", "get_current_active_user", "require_auth",
    "oauth2_scheme", "jwt_required", "verify_token",
}

_RATE_LIMIT_SIGNALS = {
    "Limiter", "RateLimiter", "limiter", "rate_limit",
}

_DATA_CALLS = {
    "execute", "fetch_one", "fetch_all", "get", "set", "hget", "hset",
    "query", "first", "all", "filter", "commit", "rollback", "save",
    "find_one", "find", "insert_one", "update_one",
}


@dataclass
class FlowStep:
    """One step in the request processing pipeline."""
    name: str
    kind: str    # edge|cors|auth|rate_limit|handler|cache|database|response
    description: str
    files: list[str] = field(default_factory=list)
    is_conditional: bool = False


@dataclass
class RequestFlow:
    """The complete request processing flow for one endpoint archetype."""
    archetype: str           # e.g. "authenticated", "public", "admin"
    steps: list[FlowStep]
    description: str = ""


class FlowBuilder:
    """Builds deterministic request flows from IR + evidence."""

    def build(
        self,
        codebase_ir: "CodebaseIR",
        tech_nodes: "list[Any] | None" = None,
    ) -> list[RequestFlow]:
        """Build request flows from CodebaseIR."""
        if tech_nodes is None:
            tech_nodes = []

        steps: list[FlowStep] = []

        # Step 1: Edge (from infra evidence - NGINX/Traefik)
        edge_techs = [t for t in tech_nodes if getattr(t, "layer", "") == "edge"]
        if edge_techs:
            disp_name = getattr(edge_techs[0], "display_name", "Edge Proxy")
            steps.append(FlowStep(
                name=disp_name,
                kind="edge",
                description=f"Reverse proxy / load balancer ({disp_name})",
                files=[],
            ))

        # Step 2: Middleware (CORS, trusted hosts, etc.)
        middleware_files: list[str] = []
        has_cors = False
        has_session = False
        for f in getattr(codebase_ir, "files", []):
            all_calls = list(getattr(f, "global_calls", []))
            for fn in getattr(f, "functions", []):
                all_calls.extend(getattr(fn, "calls", []))
            for call in all_calls:
                callee = getattr(call, "callee", "")
                func = callee.split(".")[-1]
                if func == "add_middleware":
                    middleware_files.append(f.file_path)
                if "CORS" in callee:
                    has_cors = True
                if "Session" in callee:
                    has_session = True
            for cls in getattr(f, "classes", []):
                if getattr(cls, "name", "") in _MIDDLEWARE_CLASSES:
                    middleware_files.append(f.file_path)

        if middleware_files or has_cors:
            steps.append(FlowStep(
                name="Middleware" + (" + CORS" if has_cors else ""),
                kind="cors",
                description="Request middleware: CORS, trusted hosts, compression, etc.",
                files=list(set(middleware_files)),
            ))

        # Step 3: Auth (Depends on auth functions)
        auth_files: list[str] = []
        has_auth = False
        for f in getattr(codebase_ir, "files", []):
            for imp in getattr(f, "imports", []):
                mod = (getattr(imp, "module", "") or getattr(imp, "from_module", "") or "").lower()
                if any(p in mod for p in {"jose", "jwt", "oauth", "auth", "passlib"}):
                    has_auth = True
                    auth_files.append(f.file_path)
                    break
            if any(c in f.file_path.lower() for c in {"auth", "security", "login", "token"}):
                has_auth = True
                auth_files.append(f.file_path)

        if has_auth:
            steps.append(FlowStep(
                name="Authentication",
                kind="auth",
                description="JWT/OAuth2 token validation and user identity resolution",
                files=list(set(auth_files)),
                is_conditional=True,  # only on protected routes
            ))

        # Step 4: Rate Limiting
        rate_techs = [t for t in tech_nodes if getattr(t, "tech_id", "") in {"slowapi", "limits"}]
        has_rate_limit = bool(rate_techs)
        if not has_rate_limit:
            for f in getattr(codebase_ir, "files", []):
                for imp in getattr(f, "imports", []):
                    mod = (getattr(imp, "module", "") or getattr(imp, "from_module", "") or "").lower()
                    if any(p in mod for p in {"slowapi", "limits", "ratelimit"}):
                        has_rate_limit = True
                        break
        if has_rate_limit:
            steps.append(FlowStep(
                name="Rate Limiting",
                kind="rate_limit",
                description="Request rate limiting per IP or user token",
                files=[],
                is_conditional=True,
            ))

        # Step 5: Route Handler (business logic)
        route_files: list[str] = []
        for f in getattr(codebase_ir, "files", []):
            path_parts = f.file_path.replace("\\", "/").lower().split("/")
            if any(p in path_parts for p in {"routes", "routers", "views", "endpoints", "api"}):
                route_files.append(f.file_path)
        steps.append(FlowStep(
            name="Route Handler",
            kind="handler",
            description="Business logic handler processes the incoming request",
            files=route_files[:10],
        ))

        # Step 6: Cache (Redis)
        cache_techs = [t for t in tech_nodes if "cache" in getattr(t, "possible_roles", []) or getattr(t, "tech_id", "") == "redis"]
        if cache_techs:
            disp_name = getattr(cache_techs[0], "display_name", "Redis")
            steps.append(FlowStep(
                name=f"{disp_name} Cache",
                kind="cache",
                description="Cache read/write (read-through pattern where applicable)",
                files=[],
                is_conditional=True,
            ))

        # Step 7: Database
        db_techs = [
            t for t in tech_nodes
            if "primary_database" in getattr(t, "possible_roles", [])
            or "document_store" in getattr(t, "possible_roles", [])
        ]
        if db_techs:
            disp_name = getattr(db_techs[0], "display_name", "Database")
            steps.append(FlowStep(
                name=f"{disp_name} Database",
                kind="database",
                description="Persistent data read/write",
                files=[],
            ))

        # Build archetypes
        flows: list[RequestFlow] = []

        # Public route flow
        public_steps = [s for s in steps if s.kind != "auth" or not s.is_conditional]
        flows.append(RequestFlow(
            archetype="public",
            steps=public_steps,
            description="Unauthenticated request path",
        ))

        # Authenticated route flow (all steps)
        if has_auth:
            flows.append(RequestFlow(
                archetype="authenticated",
                steps=steps,
                description="Authenticated request path with JWT/OAuth2 validation",
            ))

        return flows
