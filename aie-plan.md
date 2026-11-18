# TwinOps — Application Intelligence Engine (AIE) Implementation Plan

> **Automatic source-code analysis to make TwinOps understand an application before managing it.**
>
> Companion to [plan.md](file:///c:/twinsops/plan.md) and [data-connector-plan.md](file:///c:/twinsops/data-connector-plan.md).
> Connectors give TwinOps **runtime telemetry**. AIE gives TwinOps **architectural knowledge**.

---

## 1. What AIE Does (One Paragraph)

When a user registers an application in TwinOps and provides its Git repository URL, the AIE clones the repo, scans every source file, parses ASTs, reads configs and docs, identifies components and their relationships, then uses the Groq LLM to generate structured, evidence-backed architectural knowledge. The result is a complete Application Descriptor + Component Knowledge Base that the Digital Twin, anomaly detector, and LLM reasoning engine can consume — **without anyone manually writing JSON descriptors**.

```
┌─────────────────────┐
│  User provides       │
│  GitHub repo URL     │
│  + email identity    │
└──────────┬──────────┘
           │
           ▼
┌──────────────────────────────────────────────────────┐
│              APPLICATION INTELLIGENCE ENGINE          │
│                                                      │
│  Clone → Filter → Discover → Parse → Identify →     │
│  Extract → Summarize → Validate → Graph → Store      │
│                                                      │
└──────────┬───────────────────────────────────────────┘
           │
           ▼
┌──────────────────────────────────────────────────────┐
│  STRUCTURED OUTPUT                                    │
│                                                      │
│  • Application Descriptor (Contract B — auto-gen)    │
│  • Component Knowledge Base (per-component details)  │
│  • Architecture Graph (nodes + edges + evidence)     │
│  • Capability Catalog (inferred — Contract C)        │
│  • Vector embeddings (for LLM retrieval)             │
└──────────────────────────────────────────────────────┘
           │
           ▼
   Digital Twin    LLM Engine    Anomaly Detector    Dashboard
```

---

## 2. Repository Access Design

### 2.1 Access Model

```
┌─────────────────────────────────────────────────────────┐
│                      FRONTEND                            │
│                                                         │
│  ┌─────────────────────────────────────────────────┐    │
│  │  "Connect Application" Form                      │    │
│  │                                                  │    │
│  │  Repository URL: [ https://github.com/user/repo ]│    │
│  │  Visibility:     ( ) Public  (•) Private         │    │
│  │                                                  │    │
│  │  ── If Private ──────────────────────────────    │    │
│  │  │  Auth Method: GitHub Personal Access Token  │ │    │
│  │  │  PAT:  [ ghp_xxxxxxxxxxxxxxxxxxxx ]         │ │    │
│  │  │  ──────────────────────────────────────────  │ │    │
│  │  │                                              │ │    │
│  │  │  OR (future): "Sign in with GitHub" OAuth    │ │    │
│  │  └──────────────────────────────────────────────┘ │    │
│  │                                                  │    │
│  │  Branch: [ main ]                                │    │
│  │                                                  │    │
│  │  [ Analyze Repository ]                          │    │
│  └─────────────────────────────────────────────────┘    │
└──────────────────────┬──────────────────────────────────┘
                       │  POST /api/aie/analyze
                       │  { repo_url, branch, auth_token?, user_email }
                       ▼
┌──────────────────────────────────────────────────────────┐
│                      BACKEND                             │
│                                                          │
│  1. Validate repo URL format                             │
│  2. If private → use PAT to authenticate git clone       │
│  3. Clone to isolated temp workspace                     │
│  4. Start async analysis pipeline                        │
│  5. Return analysis_id for status polling                │
└──────────────────────────────────────────────────────────┘
```

### 2.2 Authentication Methods

| Method | How It Works | When To Use |
|--------|-------------|-------------|
| **Public repo** | `git clone https://github.com/user/repo.git` — no auth needed | Open-source projects |
| **GitHub PAT (Personal Access Token)** | User generates a PAT with `repo` scope from GitHub Settings → Developer Settings. Frontend sends it. Backend uses: `git clone https://{PAT}@github.com/user/repo.git` | Private repos — simplest method, works immediately |
| **GitHub OAuth (future)** | User clicks "Sign in with GitHub" → OAuth flow → TwinOps gets an access token scoped to repos the user has access to | Better UX, production-ready, no manual PAT management |

### 2.3 Testing Configuration

For initial development and testing:

```python
# backend/aie/config.py

class AIEConfig(BaseSettings):
    # Test user identity
    default_user_email: str = "jashwanthkv2005@gmail.com"

    # GitHub access
    github_pat: str = ""                    # Set via env var GITHUB_PAT
    github_api_base: str = "https://api.github.com"

    # Analysis workspace
    workspace_base_dir: str = "./aie_workspaces"
    max_repo_size_mb: int = 500
    max_file_count: int = 10000
    max_file_size_mb: int = 10

    # Security
    excluded_patterns: list[str] = [
        ".env", ".env.*", "*.pem", "*.key", "*.p12", "*.jks",
        "*.credential*", "*secret*", "id_rsa", "id_ed25519",
    ]

    # LLM
    groq_model: str = "llama-3.3-70b-versatile"
    max_context_tokens_per_component: int = 6000
    llm_temperature: float = 0.1

    # Analysis
    supported_languages: list[str] = ["python", "javascript", "typescript", "java"]
    analysis_timeout_minutes: int = 30
```

### 2.4 Access Validation Flow

```python
async def validate_repo_access(repo_url: str, auth_token: str | None, user_email: str) -> RepoAccessResult:
    """
    Verify we can access the repository before starting analysis.
    """
    # 1. Parse and validate URL
    parsed = parse_github_url(repo_url)  # → owner, repo_name
    if not parsed:
        return RepoAccessResult(accessible=False, reason="Invalid GitHub URL")

    # 2. Check if public
    resp = await httpx.get(f"https://api.github.com/repos/{parsed.owner}/{parsed.repo}")
    if resp.status_code == 200:
        repo_info = resp.json()
        if not repo_info.get("private"):
            return RepoAccessResult(accessible=True, is_private=False, metadata=repo_info)

    # 3. If private or 404 (might be private) → need auth
    if not auth_token:
        return RepoAccessResult(
            accessible=False,
            reason="Repository is private or not found. Please provide a GitHub PAT."
        )

    # 4. Validate PAT has access
    headers = {"Authorization": f"token {auth_token}"}
    resp = await httpx.get(
        f"https://api.github.com/repos/{parsed.owner}/{parsed.repo}",
        headers=headers
    )
    if resp.status_code == 200:
        repo_info = resp.json()
        # 5. Verify user email matches a collaborator
        collaborators_resp = await httpx.get(
            f"https://api.github.com/repos/{parsed.owner}/{parsed.repo}/collaborators",
            headers=headers
        )
        if collaborators_resp.status_code == 200:
            return RepoAccessResult(accessible=True, is_private=True, metadata=repo_info)

    # 6. Access denied
    return RepoAccessResult(
        accessible=False,
        reason=f"Cannot access repo. Ensure {user_email} is a collaborator and the PAT has 'repo' scope."
    )
```

### 2.5 Security Rules (Non-Negotiable)

| # | Rule | Enforcement |
|---|------|-------------|
| S1 | **Never execute repository code** | No `eval`, `exec`, `subprocess` on repo files; AST parsing only |
| S2 | **Never expose secrets to LLM** | Filter `.env`, keys, credentials BEFORE analysis; never include in prompts |
| S3 | **Isolated workspace per analysis** | Clone into temp dir; delete after analysis completes |
| S4 | **PAT stored only in memory** | Never persist PATs to disk or DB; used only for clone then discarded |
| S5 | **Path traversal protection** | Validate all file paths stay within workspace; reject `../` patterns |
| S6 | **Size limits** | Reject repos > 500MB; skip files > 10MB; limit total file count |
| S7 | **Timeout** | Kill analysis after 30 minutes |

---

## 3. System Architecture

### 3.1 AIE Within TwinOps

```
┌────────────────────────────────────────────────────────────────────────┐
│                          TWINOPS PLATFORM                             │
│                                                                        │
│  ┌─────────────────────────┐     ┌──────────────────────────────────┐ │
│  │  CONNECTOR MANAGER       │     │  APPLICATION INTELLIGENCE ENGINE │ │
│  │  (Runtime Telemetry)     │     │  (Source-Code Knowledge)         │ │
│  │                          │     │                                  │ │
│  │  • CPU, Memory, Latency  │     │  • Components & relationships   │ │
│  │  • Pool metrics          │     │  • Implementation details       │ │
│  │  • Logs                  │     │  • Configurations from source   │ │
│  │  • Live health           │     │  • Failure patterns             │ │
│  │                          │     │  • Adaptation opportunities     │ │
│  │  Runs: continuously      │     │  Runs: on repo connect/change   │ │
│  └────────────┬─────────────┘     └──────────────┬───────────────────┘ │
│               │                                  │                     │
│               └──────────────┬───────────────────┘                     │
│                              │                                         │
│                              ▼                                         │
│               ┌──────────────────────────────┐                         │
│               │     DIGITAL TWIN MANAGER     │                         │
│               │                              │                         │
│               │  Runtime state (from Conn.)  │                         │
│               │  +                           │                         │
│               │  Architecture (from AIE)     │                         │
│               │  =                           │                         │
│               │  Complete application model  │                         │
│               └──────────────┬───────────────┘                         │
│                              │                                         │
│               ┌──────────────▼───────────────┐                         │
│               │   LLM / MAPE-K / Dashboard   │                         │
│               └──────────────────────────────┘                         │
└────────────────────────────────────────────────────────────────────────┘
```

**Key principle**: AIE and Connector Manager are **independent**. AIE provides architecture knowledge (static). Connectors provide runtime metrics (dynamic). The Digital Twin combines both.

### 3.2 AIE Internal Pipeline

```
  POST /api/aie/analyze { repo_url, branch, auth_token, user_email }
       │
       ▼
  ┌────────────────────────────────────────────────────────────────┐
  │  MODULE 1: Repository Ingestion Manager                       │
  │  • Validate access (public/PAT)                               │
  │  • git clone to isolated workspace                            │
  │  • Record metadata (commit, branch, timestamp)                │
  └───────────────────────┬────────────────────────────────────────┘
                          │
                          ▼
  ┌────────────────────────────────────────────────────────────────┐
  │  MODULE 2: File Classifier                                     │
  │  • Exclude: .git, node_modules, venv, build, binaries         │
  │  • Classify: source, docs, config, infra, tests, migrations   │
  │  • Build file inventory with hashes                            │
  └───────────────────────┬────────────────────────────────────────┘
                          │
                          ▼
  ┌────────────────────────────────────────────────────────────────┐
  │  MODULE 3: Project Discovery Engine                            │
  │  • Detect languages (Python, JS/TS, Java)                     │
  │  • Detect frameworks (FastAPI, Express, Spring Boot, etc.)    │
  │  • Detect DB, cache, queue, infrastructure technologies       │
  │  • Read: package.json, requirements.txt, pom.xml, Dockerfile  │
  └───────────────────────┬────────────────────────────────────────┘
                          │
                          ▼
  ┌────────────────────────────────────────────────────────────────┐
  │  MODULE 4: Documentation & Configuration Analyzer              │
  │  • Parse README, architecture docs, API specs                  │
  │  • Extract configs: DB connections, pool sizes, timeouts       │
  │  • Detect discrepancies between docs and code                  │
  │  • Strip secrets before storing                                │
  └───────────────────────┬────────────────────────────────────────┘
                          │
                          ▼
  ┌────────────────────────────────────────────────────────────────┐
  │  MODULE 5: Static Code Analysis Engine                         │
  │  • Python: ast module → classes, functions, imports, calls     │
  │  • JS/TS: tree-sitter → routes, middleware, exports            │
  │  • Java: tree-sitter → annotations, beans, DI                 │
  │  • Common IR (Intermediate Representation) for all languages   │
  └───────────────────────┬────────────────────────────────────────┘
                          │
                          ▼
  ┌────────────────────────────────────────────────────────────────┐
  │  MODULE 6: Component Identification Engine                     │
  │  • Identify: web tier, service, repo, DB, pool, cache, queue  │
  │  • Multi-evidence scoring (not just by class name)             │
  │  • Confidence score per component                              │
  └───────────────────────┬────────────────────────────────────────┘
                          │
                          ▼
  ┌────────────────────────────────────────────────────────────────┐
  │  MODULE 7: Dependency & Relationship Analyzer                  │
  │  • Import graph → call graph → dependency chain                │
  │  • calls, depends_on, uses, reads_from, writes_to, etc.       │
  │  • Confirmed vs inferred relationships                         │
  └───────────────────────┬────────────────────────────────────────┘
                          │
                          ▼
  ┌────────────────────────────────────────────────────────────────┐
  │  MODULE 8: Component Knowledge Extractor                       │
  │  • Per-component LLM analysis (Groq API)                       │
  │  • Structured JSON output with schema validation               │
  │  • Purpose, behavior, configs, failure scenarios, metrics      │
  │  • Source-file + line evidence for every claim                  │
  └───────────────────────┬────────────────────────────────────────┘
                          │
                          ▼
  ┌────────────────────────────────────────────────────────────────┐
  │  MODULE 9: Technical Knowledge Enrichment                      │
  │  • Match components to known technology patterns               │
  │  • Redis eviction, HikariCP pool behavior, SQLAlchemy pooling  │
  │  • Attach diagnostic playbooks and adaptation guidance         │
  └───────────────────────┬────────────────────────────────────────┘
                          │
                          ▼
  ┌────────────────────────────────────────────────────────────────┐
  │  MODULE 10: Knowledge Validation & Evidence Manager             │
  │  • Tag: STATIC_ANALYSIS, DOCUMENTATION, CONFIGURATION,         │
  │         LLM_INFERENCE, RUNTIME_OBSERVATION                     │
  │  • Record: file, line range, confidence, extraction method     │
  │  • Detect doc ↔ code discrepancies                             │
  │  • unknown ≠ false; missing ≠ zero                              │
  └───────────────────────┬────────────────────────────────────────┘
                          │
                          ▼
  ┌────────────────────────────────────────────────────────────────┐
  │  MODULE 11: Architecture Graph Generator                       │
  │  • Nodes: components with type, technology, state              │
  │  • Edges: relationships with type, evidence                    │
  │  • Format compatible with Digital Twin graph schema             │
  │  • Auto-generates Application Descriptor (Contract B)          │
  └───────────────────────┬────────────────────────────────────────┘
                          │
                          ▼
  ┌────────────────────────────────────────────────────────────────┐
  │  MODULE 12: Knowledge Persistence                              │
  │  • SQLite :memory: → components, relationships, evidence       │
  │  • Architecture graph → Twin Manager                           │
  │  • Vector store → semantic retrieval for LLM prompts           │
  └────────────────────────────────────────────────────────────────┘
```

---

## 4. Data Models

### 4.1 Analysis Run

```python
class AnalysisRun(BaseModel):
    analysis_id: str                          # UUID
    application_id: str                       # Links to TwinOps app registry
    repo_url: str
    branch: str
    commit_hash: str
    user_email: str                           # Who triggered it
    status: Literal["pending", "cloning", "analyzing", "completed", "failed"]
    started_at: datetime
    completed_at: datetime | None = None
    error_message: str | None = None
    file_count: int = 0
    component_count: int = 0
    relationship_count: int = 0
    analysis_version: int = 1                 # Incremented on re-analysis
    previous_analysis_id: str | None = None   # For incremental
```

### 4.2 File Inventory Entry

```python
class FileCategory(str, Enum):
    SOURCE_CODE = "source_code"
    DOCUMENTATION = "documentation"
    DEPENDENCY_MANIFEST = "dependency_manifest"
    CONFIGURATION = "configuration"
    INFRASTRUCTURE = "infrastructure"
    DATABASE_SCHEMA = "database_schema"
    TEST = "test"
    DEPLOYMENT = "deployment"
    OTHER = "other"

class FileEntry(BaseModel):
    file_path: str                            # Relative to repo root
    language: str | None                      # python, javascript, java, etc.
    category: FileCategory
    size_bytes: int
    content_hash: str                         # SHA-256, for incremental
    included_in_analysis: bool
    exclusion_reason: str | None = None
```

### 4.3 Project Technology Profile

```python
class TechnologyProfile(BaseModel):
    languages: list[LanguageInfo]              # [{name, version, file_count, percentage}]
    frameworks: list[FrameworkInfo]            # [{name, version, evidence_file}]
    databases: list[TechnologyInfo]            # [{name, version, detected_from}]
    caches: list[TechnologyInfo]
    message_brokers: list[TechnologyInfo]
    web_servers: list[TechnologyInfo]
    background_workers: list[TechnologyInfo]
    infrastructure: list[TechnologyInfo]       # Docker, K8s, Terraform, etc.
    dependency_managers: list[str]             # pip, npm, maven, gradle
    entry_points: list[EntryPoint]             # [{file, type: "main"|"server"|"worker"}]
```

### 4.4 Discovered Component

```python
class EvidenceType(str, Enum):
    STATIC_ANALYSIS = "static_analysis"
    DOCUMENTATION = "documentation"
    CONFIGURATION = "configuration"
    LLM_INFERENCE = "llm_inference"
    RUNTIME_OBSERVATION = "runtime_observation"

class SourceEvidence(BaseModel):
    file: str
    start_line: int
    end_line: int
    extraction_method: EvidenceType
    confidence: float                         # 0.0 – 1.0
    snippet: str | None = None                # Relevant code excerpt (secrets stripped)

class DiscoveredComponent(BaseModel):
    component_id: str                         # UUID
    name: str                                 # e.g., "Redis Product Cache"
    logical_type: ComponentType               # web_tier, service_layer, cache, etc.
    technology: str                           # e.g., "Redis", "HikariCP"
    library: str | None                       # e.g., "redis-py", "pg.Pool"
    version: str | None

    # Knowledge
    responsibility: str                       # What this component does
    implementation_summary: str               # How it's implemented
    important_classes: list[str]
    important_functions: list[str]

    # Behavior (extracted from code)
    behavior: dict                            # Component-specific, e.g., cache strategy, pool config
    configuration: dict                       # Extracted config values from source
    config_source: dict                       # Where each config value was found

    # Operational intelligence
    failure_scenarios: list[str]
    monitoring_requirements: list[str]        # Which metrics matter
    adaptation_opportunities: list[str]       # What could be tuned
    adaptation_constraints: list[str]         # What limits tuning

    # Relationships
    dependencies: list[str]                   # component_ids this depends on
    dependents: list[str]                     # component_ids that depend on this

    # Evidence
    source_evidence: list[SourceEvidence]
    confidence_score: float                   # Overall confidence (0.0 – 1.0)

    # Metadata
    analysis_id: str
    discovered_at: datetime
```

### 4.5 Discovered Relationship

```python
class RelationshipConfidence(str, Enum):
    CONFIRMED = "confirmed"                   # Found in imports + call graph
    INFERRED = "inferred"                     # LLM-inferred or naming-based

class DiscoveredRelationship(BaseModel):
    relationship_id: str
    from_component: str                       # component_id
    to_component: str                         # component_id
    relation_type: RelationType               # calls, depends_on, uses, etc.
    confidence: RelationshipConfidence
    evidence: list[SourceEvidence]
    description: str | None = None            # e.g., "OrderService calls OrderRepository.save()"
```

### 4.6 LLM Component Analysis Schema (What Groq Must Return)

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "type": "object",
  "required": ["component_id", "responsibility", "implementation_summary",
               "behavior", "failure_scenarios", "monitoring_requirements"],
  "properties": {
    "component_id": { "type": "string" },
    "responsibility": { "type": "string", "maxLength": 500 },
    "implementation_summary": { "type": "string", "maxLength": 1000 },
    "behavior": {
      "type": "object",
      "properties": {
        "strategy": { "type": "string" },
        "patterns": { "type": "array", "items": { "type": "string" } },
        "concurrency": { "type": "string" },
        "error_handling": { "type": "string" }
      }
    },
    "failure_scenarios": {
      "type": "array",
      "items": { "type": "string" },
      "minItems": 1, "maxItems": 10
    },
    "monitoring_requirements": {
      "type": "array",
      "items": { "type": "string" },
      "minItems": 1, "maxItems": 10
    },
    "adaptation_opportunities": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "action": { "type": "string" },
          "target_config": { "type": "string" },
          "risk": { "type": "string", "enum": ["low", "medium", "high"] },
          "reversible": { "type": "boolean" }
        }
      }
    },
    "configuration_findings": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "key": { "type": "string" },
          "value": { "type": "string" },
          "source_file": { "type": "string" },
          "line": { "type": "integer" }
        }
      }
    },
    "confidence": { "type": "number", "minimum": 0, "maximum": 1 }
  },
  "additionalProperties": false
}
```

---

## 5. API Design

### 5.1 AIE Endpoints

```
# ── Analysis Management ──

POST   /api/aie/analyze
       Body: { repo_url, branch, auth_token?, user_email }
       → Starts async analysis, returns { analysis_id, status: "pending" }

GET    /api/aie/analyses
       → List all analysis runs (paginated)

GET    /api/aie/analyses/{analysis_id}
       → Analysis status, progress, summary

GET    /api/aie/analyses/{analysis_id}/logs
       → Streaming analysis progress logs

DELETE /api/aie/analyses/{analysis_id}
       → Cancel a running analysis


# ── Results ──

GET    /api/aie/apps/{app_id}/profile
       → Technology profile (languages, frameworks, infra)

GET    /api/aie/apps/{app_id}/components
       → All discovered components

GET    /api/aie/apps/{app_id}/components/{component_id}
       → Full component knowledge (detail view)

GET    /api/aie/apps/{app_id}/relationships
       → All discovered relationships

GET    /api/aie/apps/{app_id}/graph
       → Architecture graph (nodes + edges, Twin-compatible format)

GET    /api/aie/apps/{app_id}/descriptor
       → Auto-generated Application Descriptor (Contract B)

GET    /api/aie/apps/{app_id}/capabilities
       → Inferred Capability Catalog (Contract C)

GET    /api/aie/apps/{app_id}/files
       → File inventory with categories

GET    /api/aie/apps/{app_id}/configs
       → All extracted configurations with sources

GET    /api/aie/apps/{app_id}/discrepancies
       → Documentation ↔ code mismatches


# ── Incremental Analysis ──

POST   /api/aie/apps/{app_id}/reanalyze
       Body: { branch?, force_full?: false }
       → Incremental re-analysis (only changed files)

GET    /api/aie/apps/{app_id}/changes
       → Diff between current and previous analysis
```

### 5.2 WebSocket for Analysis Progress

```
WS     /ws/aie/analyses/{analysis_id}
       → Real-time progress updates:
         { "step": "cloning", "progress": 10, "message": "Cloning repository..." }
         { "step": "filtering", "progress": 20, "message": "Classified 347 files" }
         { "step": "discovery", "progress": 30, "message": "Found: Python, FastAPI, PostgreSQL" }
         { "step": "ast_analysis", "progress": 50, "message": "Parsing 128 Python files..." }
         { "step": "components", "progress": 60, "message": "Identified 7 components" }
         { "step": "llm_analysis", "progress": 75, "message": "Analyzing: Redis Cache (3/7)..." }
         { "step": "graph", "progress": 90, "message": "Generating architecture graph" }
         { "step": "completed", "progress": 100, "message": "Analysis complete: 7 components, 12 relationships" }
```

---

## 6. Project File Structure

```
backend/
├── aie/                              # Application Intelligence Engine
│   ├── __init__.py
│   ├── config.py                     # AIE-specific settings
│   ├── pipeline.py                   # Main analysis orchestrator
│   │
│   ├── ingestion/                    # Module 1: Repository Ingestion
│   │   ├── __init__.py
│   │   ├── manager.py                # Clone, workspace setup, cleanup
│   │   ├── github_client.py          # GitHub API client (validate, clone)
│   │   ├── access.py                 # Auth validation (public/PAT/OAuth)
│   │   └── security.py               # Path traversal, size limits, sandbox
│   │
│   ├── classifier/                   # Module 2: File Classifier
│   │   ├── __init__.py
│   │   ├── filter.py                 # Exclude patterns (.git, node_modules, etc.)
│   │   ├── classifier.py             # Categorize: source, config, docs, infra
│   │   ├── language_detector.py      # Detect file language by extension + content
│   │   └── inventory.py              # Build file inventory with hashes
│   │
│   ├── discovery/                    # Module 3: Project Discovery
│   │   ├── __init__.py
│   │   ├── engine.py                 # Orchestrate all detectors
│   │   ├── detectors/
│   │   │   ├── __init__.py
│   │   │   ├── python.py             # requirements.txt, pyproject.toml, setup.py
│   │   │   ├── javascript.py         # package.json, package-lock.json
│   │   │   ├── java.py               # pom.xml, build.gradle
│   │   │   ├── infrastructure.py     # Dockerfile, docker-compose, K8s, Terraform
│   │   │   └── database.py           # Detect DB from configs, imports, connection strings
│   │   └── models.py                 # TechnologyProfile, FrameworkInfo, etc.
│   │
│   ├── docs_analyzer/               # Module 4: Documentation & Config Analysis
│   │   ├── __init__.py
│   │   ├── doc_parser.py             # Parse README, architecture docs
│   │   ├── config_extractor.py       # Extract DB config, pool sizes, timeouts
│   │   ├── secret_filter.py          # Strip secrets before storage
│   │   └── discrepancy.py            # Detect doc ↔ code mismatches
│   │
│   ├── static_analysis/             # Module 5: Static Code Analysis
│   │   ├── __init__.py
│   │   ├── engine.py                 # Language-agnostic analysis orchestrator
│   │   ├── ir.py                     # Common Intermediate Representation
│   │   ├── parsers/
│   │   │   ├── __init__.py
│   │   │   ├── python_parser.py      # Python ast module
│   │   │   ├── js_parser.py          # Tree-sitter for JS/TS
│   │   │   └── java_parser.py        # Tree-sitter for Java
│   │   └── extractors/
│   │       ├── __init__.py
│   │       ├── imports.py            # Import graph extraction
│   │       ├── calls.py              # Call graph extraction
│   │       ├── classes.py            # Class/function/method extraction
│   │       ├── decorators.py         # Decorator/annotation extraction
│   │       └── framework_rules.py    # Extensible framework-specific rules
│   │
│   ├── components/                   # Module 6: Component Identification
│   │   ├── __init__.py
│   │   ├── identifier.py             # Multi-evidence component identification
│   │   ├── scoring.py                # Confidence scoring
│   │   ├── rules/
│   │   │   ├── __init__.py
│   │   │   ├── web_tier.py           # Detect API routes, HTTP handlers
│   │   │   ├── service_layer.py      # Detect service classes
│   │   │   ├── data_access.py        # Detect repositories, DAOs
│   │   │   ├── database.py           # Detect DB connections, engines
│   │   │   ├── cache.py              # Detect cache usage (Redis, Memcached)
│   │   │   ├── queue.py              # Detect message queues
│   │   │   └── external_api.py       # Detect external API clients
│   │   └── models.py                 # DiscoveredComponent model
│   │
│   ├── relationships/               # Module 7: Dependency & Relationship Analyzer
│   │   ├── __init__.py
│   │   ├── analyzer.py               # Build dependency graph from evidence
│   │   ├── import_graph.py           # Import-based relationships
│   │   ├── call_graph.py             # Call-based relationships
│   │   └── models.py                 # DiscoveredRelationship model
│   │
│   ├── knowledge/                   # Module 8: Component Knowledge Extractor
│   │   ├── __init__.py
│   │   ├── extractor.py              # Per-component LLM analysis orchestrator
│   │   ├── context_builder.py        # Build LLM context (relevant files, configs, deps)
│   │   ├── prompts.py                # Prompt templates for component analysis
│   │   ├── schema.py                 # JSON schema for LLM response validation
│   │   └── validator.py              # Validate + sanitize LLM output
│   │
│   ├── enrichment/                  # Module 9: Technical Knowledge Enrichment
│   │   ├── __init__.py
│   │   ├── enricher.py               # Match components to known patterns
│   │   └── knowledge_modules/
│   │       ├── __init__.py
│   │       ├── redis.py              # Redis failure patterns, diagnostics
│   │       ├── postgresql.py         # PostgreSQL pool behavior, diagnostics
│   │       ├── hikaricp.py           # HikariCP pool tuning
│   │       ├── sqlalchemy.py         # SQLAlchemy pool behavior
│   │       └── celery.py             # Celery worker patterns
│   │
│   ├── validation/                  # Module 10: Evidence Manager
│   │   ├── __init__.py
│   │   ├── evidence.py               # SourceEvidence model + tracking
│   │   ├── confidence.py             # Confidence calculation
│   │   └── discrepancy.py            # Detect inconsistencies
│   │
│   ├── graph/                       # Module 11: Architecture Graph Generator
│   │   ├── __init__.py
│   │   ├── generator.py              # Build graph from components + relationships
│   │   ├── twin_format.py            # Convert to Digital Twin graph schema
│   │   └── descriptor_gen.py         # Auto-generate Contract B (App Descriptor)
│   │
│   ├── persistence/                 # Module 12: Knowledge Persistence
│   │   ├── __init__.py
│   │   ├── store.py                  # SQLite in-memory storage
│   │   ├── models.py                 # SQLAlchemy models for all entities
│   │   ├── vector_store.py           # Embeddings for semantic retrieval
│   │   └── queries.py                # Query helpers
│   │
│   └── api/                         # AIE API Routes
│       ├── __init__.py
│       ├── routes.py                 # All /api/aie/* endpoints
│       └── websocket.py              # WS /ws/aie/* for progress updates

frontend/src/
├── components/
│   └── aie/                         # AIE Dashboard Components
│       ├── ConnectRepo.tsx           # Repository connection form
│       ├── AnalysisProgress.tsx      # Real-time analysis progress
│       ├── TechProfile.tsx           # Languages, frameworks, infra display
│       ├── ArchitectureGraph.tsx     # Interactive component graph (react-flow)
│       ├── ComponentDetail.tsx       # Detailed component knowledge view
│       ├── ComponentList.tsx         # All discovered components
│       ├── RelationshipList.tsx      # All discovered relationships
│       ├── FileInventory.tsx         # Analyzed files list
│       ├── ConfigFindings.tsx        # Extracted configurations
│       ├── DiscrepancyList.tsx       # Doc ↔ code mismatches
│       └── SourceEvidence.tsx        # Evidence viewer with file links
├── api/
│   └── aie.ts                       # API client for AIE endpoints
├── hooks/
│   └── useAnalysis.ts               # Hook for analysis progress WebSocket
├── store/
│   └── aieStore.ts                  # Zustand store for AIE state
└── types/
    └── aie.ts                       # TypeScript types for AIE models
```

---

## 7. Core Implementation Details

### 7.1 Pipeline Orchestrator

```python
# backend/aie/pipeline.py

class AIEPipeline:
    """
    Orchestrates the 12-module analysis pipeline.
    Runs as an async background task.
    """

    async def analyze(self, request: AnalysisRequest) -> AnalysisRun:
        run = AnalysisRun(analysis_id=uuid4(), status="pending", ...)
        try:
            # Module 1: Clone repo
            await self._update_status(run, "cloning", 5)
            workspace = await self.ingestion.clone(
                repo_url=request.repo_url,
                branch=request.branch,
                auth_token=request.auth_token,
            )

            # Module 2: Filter & classify files
            await self._update_status(run, "filtering", 15)
            inventory = await self.classifier.classify(workspace)

            # Module 3: Discover tech stack
            await self._update_status(run, "discovery", 25)
            profile = await self.discovery.discover(workspace, inventory)

            # Module 4: Parse docs & configs
            await self._update_status(run, "docs_config", 35)
            docs_knowledge = await self.docs_analyzer.analyze(workspace, inventory)

            # Module 5: AST analysis
            await self._update_status(run, "ast_analysis", 45)
            code_ir = await self.static_analysis.analyze(workspace, inventory, profile)

            # Module 6: Identify components
            await self._update_status(run, "components", 55)
            components = await self.component_identifier.identify(
                code_ir, profile, docs_knowledge
            )

            # Module 7: Discover relationships
            await self._update_status(run, "relationships", 65)
            relationships = await self.relationship_analyzer.analyze(
                components, code_ir
            )

            # Module 8: LLM knowledge extraction (per component)
            await self._update_status(run, "llm_analysis", 70)
            for i, component in enumerate(components):
                progress = 70 + int((i / len(components)) * 15)
                await self._update_status(
                    run, "llm_analysis", progress,
                    f"Analyzing: {component.name} ({i+1}/{len(components)})"
                )
                context = self.context_builder.build(component, code_ir, docs_knowledge)
                knowledge = await self.knowledge_extractor.extract(component, context)
                component.update_with_knowledge(knowledge)

            # Module 9: Enrich with technical knowledge
            await self._update_status(run, "enrichment", 87)
            await self.enricher.enrich(components, profile)

            # Module 10: Validate evidence
            await self._update_status(run, "validation", 90)
            await self.evidence_manager.validate(components, relationships)

            # Module 11: Generate architecture graph
            await self._update_status(run, "graph", 93)
            graph = self.graph_generator.generate(components, relationships)
            descriptor = self.graph_generator.to_descriptor(graph)
            capabilities = self.graph_generator.infer_capabilities(components)

            # Module 12: Persist
            await self._update_status(run, "persisting", 97)
            await self.persistence.store_all(
                run, inventory, profile, components,
                relationships, graph, descriptor, capabilities
            )

            # Done
            await self._update_status(run, "completed", 100)
            run.status = "completed"
            run.component_count = len(components)
            run.relationship_count = len(relationships)

        except Exception as e:
            run.status = "failed"
            run.error_message = str(e)
            logger.exception(f"Analysis failed: {e}")

        finally:
            # Cleanup workspace (delete cloned repo)
            await self.ingestion.cleanup(workspace)

        return run
```

### 7.2 LLM Prompt for Component Analysis (Groq)

```python
# backend/aie/knowledge/prompts.py

COMPONENT_ANALYSIS_PROMPT = """You are analyzing a software component for TwinOps, a digital twin platform.

## Component Information
- Name: {component_name}
- Type: {component_type}
- Technology: {technology}

## Source Code
{source_code_excerpts}

## Related Configuration
{configuration_excerpts}

## Related Documentation
{documentation_excerpts}

## Related Components
{related_components_summary}

## Instructions
Analyze this component and provide a structured JSON response.

Rules:
1. Only state facts supported by the provided source code, configuration, or documentation.
2. If information is not available, omit the field or use null. Do NOT invent values.
3. For configuration values, always cite the source file and line number.
4. Distinguish between what the code actually does vs. what documentation says it does.
5. Never include secrets, passwords, tokens, or API keys in your response.

Respond with ONLY valid JSON matching this schema:
{json_schema}
"""
```

### 7.3 Python AST Parser (Module 5)

```python
# backend/aie/static_analysis/parsers/python_parser.py

import ast
from pathlib import Path

class PythonASTParser:
    """
    Parse Python files using the built-in ast module.
    Extract structural information without executing code.
    """

    def parse_file(self, file_path: Path) -> PythonFileIR:
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            source = f.read()

        try:
            tree = ast.parse(source, filename=str(file_path))
        except SyntaxError:
            return PythonFileIR(file=str(file_path), parse_error=True)

        return PythonFileIR(
            file=str(file_path),
            imports=self._extract_imports(tree),
            classes=self._extract_classes(tree),
            functions=self._extract_functions(tree),
            global_variables=self._extract_globals(tree),
            decorators=self._extract_decorators(tree),
            function_calls=self._extract_calls(tree),
            framework_patterns=self._detect_patterns(tree),
        )

    def _extract_imports(self, tree: ast.Module) -> list[ImportInfo]:
        imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports.append(ImportInfo(
                        module=alias.name,
                        alias=alias.asname,
                        line=node.lineno
                    ))
            elif isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    imports.append(ImportInfo(
                        module=f"{node.module}.{alias.name}" if node.module else alias.name,
                        from_module=node.module,
                        name=alias.name,
                        alias=alias.asname,
                        line=node.lineno
                    ))
        return imports

    def _extract_classes(self, tree: ast.Module) -> list[ClassInfo]:
        classes = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                classes.append(ClassInfo(
                    name=node.name,
                    bases=[self._name_from_node(b) for b in node.bases],
                    methods=[m.name for m in node.body if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))],
                    decorators=[self._name_from_node(d) for d in node.decorator_list],
                    line=node.lineno,
                    end_line=node.end_lineno,
                ))
        return classes

    def _detect_patterns(self, tree: ast.Module) -> list[FrameworkPattern]:
        """Detect framework-specific patterns: FastAPI routes, Flask blueprints, etc."""
        patterns = []
        for node in ast.walk(tree):
            # FastAPI route decorators: @app.get("/path")
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for dec in node.decorator_list:
                    if isinstance(dec, ast.Call) and self._is_route_decorator(dec):
                        patterns.append(FrameworkPattern(
                            type="api_route",
                            framework="fastapi",
                            details={"method": self._get_http_method(dec), "path": self._get_route_path(dec)},
                            line=node.lineno,
                        ))
            # SQLAlchemy engine creation: create_engine(...)
            if isinstance(node, ast.Call) and self._is_call_to(node, "create_engine"):
                patterns.append(FrameworkPattern(
                    type="database_connection",
                    framework="sqlalchemy",
                    details={},
                    line=node.lineno,
                ))
        return patterns
```

### 7.4 Component Identification Scoring

```python
# backend/aie/components/scoring.py

class ComponentScorer:
    """
    Score component identification using multiple evidence sources.
    A component is NOT identified by name alone.
    """

    def score(self, candidate: ComponentCandidate) -> float:
        scores = []

        # Evidence 1: Import analysis (weight 0.25)
        if candidate.imports_match_pattern:
            scores.append(("imports", 0.25, candidate.import_confidence))

        # Evidence 2: Class/function behavior (weight 0.30)
        if candidate.code_behavior_matches:
            scores.append(("behavior", 0.30, candidate.behavior_confidence))

        # Evidence 3: Configuration references (weight 0.20)
        if candidate.has_config_evidence:
            scores.append(("config", 0.20, candidate.config_confidence))

        # Evidence 4: Framework conventions (weight 0.15)
        if candidate.follows_framework_convention:
            scores.append(("convention", 0.15, candidate.convention_confidence))

        # Evidence 5: Documentation mention (weight 0.10)
        if candidate.mentioned_in_docs:
            scores.append(("docs", 0.10, candidate.doc_confidence))

        # Weighted average
        if not scores:
            return 0.0
        total_weight = sum(w for _, w, _ in scores)
        return sum(w * c for _, w, c in scores) / total_weight
```

---

## 8. Auto-Generated Outputs

### 8.1 Auto-Generated Application Descriptor (Contract B)

AIE replaces the manual descriptor from `data-connector-plan.md`. Instead of the user writing JSON, AIE produces it:

```python
# backend/aie/graph/descriptor_gen.py

def generate_descriptor(app_id: str, components: list[DiscoveredComponent],
                        relationships: list[DiscoveredRelationship],
                        profile: TechnologyProfile) -> ApplicationDescriptor:
    """
    Auto-generate the Application Descriptor (Contract B)
    from AIE's discovered components and relationships.
    """
    return ApplicationDescriptor(
        application_id=app_id,
        name=profile.app_name or app_id,
        framework=profile.primary_framework,
        version=profile.framework_version,
        components=[
            Component(
                id=c.component_id,
                type=c.logical_type,
                technology=c.technology,
            )
            for c in components
        ],
        relationships=[
            Relationship(
                from_component=r.from_component,
                relation=r.relation_type,
                to_component=r.to_component,
            )
            for r in relationships
            if r.confidence == RelationshipConfidence.CONFIRMED
        ],
        current_config={
            c.component_id: c.configuration
            for c in components
            if c.configuration
        },
    )
```

### 8.2 Inferred Capability Catalog (Contract C)

```python
def infer_capabilities(components: list[DiscoveredComponent]) -> CapabilityCatalog:
    """
    Infer what adaptations TwinOps can perform, based on discovered components.
    """
    actions = []
    for c in components:
        for opp in c.adaptation_opportunities:
            if opp.get("reversible") and c.confidence_score >= 0.7:
                actions.append(AdaptationAction(
                    action=opp["action"],
                    target=c.component_id,
                    constraints=ActionConstraints(
                        min=opp.get("min", 1),
                        max=opp.get("max", 100),
                        max_delta=opp.get("max_delta", 10),
                    ),
                    reversible=opp.get("reversible", True),
                    requires_restart=opp.get("requires_restart", False),
                ))
    return CapabilityCatalog(application_id=components[0].analysis_id, actions=actions)
```

---

## 9. Incremental Analysis

```
Full Analysis (first run):
  Clone → Filter → Discover → Parse ALL → Identify → Extract → Store

Incremental Analysis (subsequent runs):
  1. Fetch latest commit hash
  2. git diff previous_hash..current_hash → changed files
  3. Look up: which components used those files as evidence?
  4. Re-analyze ONLY those components
  5. Re-run relationship analysis for affected components
  6. Update graph nodes/edges
  7. Update vector embeddings
  8. Store new analysis version; keep previous for comparison

  ┌─────────┐    ┌──────────┐    ┌──────────┐    ┌───────────┐
  │ Changed  │───►│ Affected │───►│ Re-parse │───►│ Re-extract│
  │  Files   │    │Components│    │  + re-ID │    │  + re-LLM │
  └─────────┘    └──────────┘    └──────────┘    └───────────┘
       ▲                                               │
       │                                               ▼
  git diff                                    Update graph + store
  prev..curr                                  Version: N → N+1
```

---

## 10. Implementation Phases

### Phase A1 — Ingestion & Classification (Week 1)

| Task | File | Details |
|------|------|---------|
| A1.1 | `aie/config.py` | AIE settings: test email, GitHub config, limits, Groq model |
| A1.2 | `aie/ingestion/github_client.py` | GitHub API: validate repo, check access, clone |
| A1.3 | `aie/ingestion/access.py` | Public vs PAT authentication; test with `jashwanthkv2005@gmail.com` |
| A1.4 | `aie/ingestion/manager.py` | Clone to temp workspace, record metadata, cleanup |
| A1.5 | `aie/ingestion/security.py` | Size limits, path traversal check, secret file exclusion |
| A1.6 | `aie/classifier/filter.py` | Exclude .git, node_modules, venv, build, binaries |
| A1.7 | `aie/classifier/classifier.py` | Categorize files: source, doc, config, infra, test |
| A1.8 | `aie/classifier/inventory.py` | Build file inventory with SHA-256 hashes |
| A1.9 | `aie/api/routes.py` | `POST /api/aie/analyze` + `GET /api/aie/analyses/{id}` |
| A1.10 | Frontend: `ConnectRepo.tsx` | Repo URL form with public/private toggle + PAT field |

**Done when**: User submits a public GitHub repo URL → TwinOps clones it → file inventory displayed via API.

---

### Phase A2 — Discovery & Documentation (Week 2)

| Task | File | Details |
|------|------|---------|
| A2.1 | `aie/discovery/detectors/python.py` | Parse requirements.txt, pyproject.toml |
| A2.2 | `aie/discovery/detectors/javascript.py` | Parse package.json |
| A2.3 | `aie/discovery/detectors/java.py` | Parse pom.xml, build.gradle |
| A2.4 | `aie/discovery/detectors/infrastructure.py` | Parse Dockerfile, docker-compose.yml |
| A2.5 | `aie/discovery/detectors/database.py` | Detect DB from connection strings + imports |
| A2.6 | `aie/discovery/engine.py` | Orchestrate all detectors → TechnologyProfile |
| A2.7 | `aie/docs_analyzer/doc_parser.py` | Parse README + architecture docs → structured knowledge |
| A2.8 | `aie/docs_analyzer/config_extractor.py` | Extract pool sizes, timeouts, DB config from files |
| A2.9 | `aie/docs_analyzer/secret_filter.py` | Strip secrets before storage |
| A2.10 | Frontend: `TechProfile.tsx` | Display detected languages, frameworks, infra |

**Done when**: Technology profile correctly identifies languages, frameworks, and databases from a test repo.

---

### Phase A3 — AST Analysis & Component Identification (Week 3–4)

| Task | File | Details |
|------|------|---------|
| A3.1 | `aie/static_analysis/parsers/python_parser.py` | Python AST: classes, functions, imports, calls, decorators |
| A3.2 | `aie/static_analysis/parsers/js_parser.py` | JS/TS: tree-sitter based extraction |
| A3.3 | `aie/static_analysis/ir.py` | Common Intermediate Representation |
| A3.4 | `aie/static_analysis/extractors/imports.py` | Build import graph |
| A3.5 | `aie/static_analysis/extractors/calls.py` | Build call graph |
| A3.6 | `aie/static_analysis/extractors/framework_rules.py` | Extensible rules (FastAPI routes, Flask blueprints, etc.) |
| A3.7 | `aie/components/rules/*.py` | Detection rules for each component type |
| A3.8 | `aie/components/scoring.py` | Multi-evidence confidence scoring |
| A3.9 | `aie/components/identifier.py` | Orchestrate identification |
| A3.10 | Frontend: `ComponentList.tsx` | Display discovered components with confidence |

**Done when**: AST parser extracts classes/functions/imports from Python files; component identifier finds web tier, service layer, and database components from a test repo.

---

### Phase A4 — Relationships & LLM Knowledge Extraction (Week 5–6)

| Task | File | Details |
|------|------|---------|
| A4.1 | `aie/relationships/import_graph.py` | Import-based relationship discovery |
| A4.2 | `aie/relationships/call_graph.py` | Call-based relationship discovery |
| A4.3 | `aie/relationships/analyzer.py` | Combine sources, confirmed vs inferred |
| A4.4 | `aie/knowledge/context_builder.py` | Build bounded LLM context per component |
| A4.5 | `aie/knowledge/prompts.py` | Groq prompt templates for component analysis |
| A4.6 | `aie/knowledge/schema.py` | JSON schema for LLM response |
| A4.7 | `aie/knowledge/validator.py` | Validate + sanitize LLM output; discard hallucinations |
| A4.8 | `aie/knowledge/extractor.py` | Per-component LLM orchestrator (one at a time, not whole repo) |
| A4.9 | Frontend: `ComponentDetail.tsx` | Full component knowledge view |
| A4.10 | Frontend: `ArchitectureGraph.tsx` | Interactive graph (react-flow) |

**Done when**: For each component, Groq returns valid JSON with responsibility, failure scenarios, monitoring requirements; relationships form a connected graph.

---

### Phase A5 — Graph, Persistence & Integration (Week 7–8)

| Task | File | Details |
|------|------|---------|
| A5.1 | `aie/validation/evidence.py` | Evidence tracking with source file + line references |
| A5.2 | `aie/validation/confidence.py` | Overall confidence calculation |
| A5.3 | `aie/enrichment/enricher.py` | Match to known technology patterns |
| A5.4 | `aie/enrichment/knowledge_modules/*.py` | Redis, PostgreSQL, HikariCP, SQLAlchemy patterns |
| A5.5 | `aie/graph/generator.py` | Build architecture graph from components + relationships |
| A5.6 | `aie/graph/descriptor_gen.py` | Auto-generate Application Descriptor (Contract B) |
| A5.7 | `aie/graph/twin_format.py` | Convert to Digital Twin graph format |
| A5.8 | `aie/persistence/store.py` | SQLite in-memory persistence |
| A5.9 | `aie/persistence/vector_store.py` | Vector embeddings for LLM retrieval |
| A5.10 | `aie/pipeline.py` | Full pipeline orchestrator (all 12 modules) |
| A5.11 | Twin integration | AIE graph → Digital Twin Manager auto-populates twin |
| A5.12 | LLM integration | LLM Engine queries AIE knowledge during root-cause analysis |

**Done when**: Full pipeline runs end-to-end; auto-generated descriptor feeds into Digital Twin; LLM reasoning uses component knowledge for root-cause analysis.

---

### Phase A6 — Incremental Analysis & Polish (Week 9–10)

| Task | File | Details |
|------|------|---------|
| A6.1 | Incremental engine | Detect changed files, find affected components, re-analyze |
| A6.2 | Version tracking | Store analysis versions, allow comparison |
| A6.3 | Discrepancy detection | Flag doc ↔ code mismatches |
| A6.4 | WebSocket progress | Real-time analysis progress to frontend |
| A6.5 | Frontend: `AnalysisProgress.tsx` | Live progress bar with step details |
| A6.6 | Frontend: `DiscrepancyList.tsx` | Show doc/code mismatches |
| A6.7 | Frontend: `SourceEvidence.tsx` | Click-through to source evidence |
| A6.8 | Error handling | Retries, partial failure recovery, analysis status tracking |
| A6.9 | Testing | End-to-end tests with real repos |
| A6.10 | Documentation | AIE API docs, architecture guide |

**Done when**: Re-analysis runs only on changed components; dashboard shows complete architecture with evidence links; full pipeline is production-hardened.

---

## 11. Dependencies (Python Packages)

```
# AIE-specific additions to backend/requirements.txt

# Git operations
gitpython>=3.1.40           # Clone, diff, commit tracking

# AST / Static Analysis
tree-sitter>=0.21.0         # Multi-language AST parsing
tree-sitter-python>=0.21.0
tree-sitter-javascript>=0.21.0
tree-sitter-typescript>=0.21.0
tree-sitter-java>=0.21.0

# LLM (already in project)
groq>=0.4.0                 # Groq API client

# Vector Store
chromadb>=0.4.0             # Lightweight vector DB (in-process, no server)
sentence-transformers>=2.2.0 # Embeddings (or use Groq embeddings)

# File handling
chardet>=5.0.0              # Detect file encoding
python-magic>=0.4.27        # Detect file types (binary vs text)

# Config parsing
pyyaml>=6.0                 # YAML config files
toml>=0.10.2                # pyproject.toml
```

---

## 12. How AIE Connects to Existing TwinOps Modules

```
                    AIE OUTPUT                    EXISTING MODULE
                    ==========                    ===============

    Auto-generated           ───────────►    Digital Twin Manager
    Descriptor (Contract B)                  (twin/graph.py)
    + Architecture Graph                     Creates typed graph nodes & edges
                                             without manual JSON

    Component Knowledge      ───────────►    LLM Engine (llm/prompts.py)
    (failure scenarios,                      Evidence bundle now includes:
     monitoring reqs,                        "Component knowledge: Redis cache
     adaptation opps)                         with cache-aside strategy, TTL 300s,
                                              known failure: high miss rate..."

    Inferred Capabilities    ───────────►    MAPE-K Planner
    (Contract C)                             (controller/plan.py)
                                             Knows what adaptations are available
                                             without manual config

    Config Findings          ───────────►    Connector Gateway
    (pool sizes, timeouts)                   (connector/gateway.py)
                                             Cross-validates runtime metrics
                                             against source-code config

    Vector Embeddings        ───────────►    LLM Engine (llm/client.py)
                                             Retrieves relevant code/docs
                                             when diagnosing incidents
```

---

## 13. Key Design Decisions

| Decision | Choice | Reason |
|----------|--------|--------|
| GitHub PAT for private repos (initially) | Simple, works now, user controls scope | OAuth is better UX but requires GitHub App registration |
| Python `ast` for Python files | Built-in, no dependencies, no execution | tree-sitter is fallback if ast is insufficient |
| tree-sitter for JS/TS/Java | Best multi-language AST parser available | Faster than full language-server analysis |
| One LLM call per component | Bounded context, better quality | Full-repo-in-one-prompt fails on large repos |
| ChromaDB for vector store | In-process, no server needed, lightweight | Consistent with in-memory DB approach |
| Analysis runs async (background task) | Don't block the API; repos can be large | WebSocket for progress updates |
| AIE never executes repo code | Security: malicious repos can't run code | AST parsing only; no eval/exec/subprocess |
| Evidence tags on every finding | Trustworthiness: know what's fact vs inference | STATIC_ANALYSIS vs LLM_INFERENCE distinction |
| Incremental via git diff + file hashes | Don't re-analyze 5000 files when 3 changed | Major performance win on updates |
| AIE independent of Connector Manager | Different concerns, different lifecycles | Connector = runtime; AIE = source knowledge |
