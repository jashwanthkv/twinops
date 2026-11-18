# TwinOps — Project Implementation Plan

> **Self-Adaptive Digital Twin Platform for Software Performance Monitoring & Optimization**
>
> Based on the IEEE conference paper. This plan treats TwinOps as a **buildable product**, not a research prototype.

---

## 1. Project Overview

TwinOps is a **standalone platform** that sits **outside** any application it monitors. It watches applications through **universal connectors** (language/framework-agnostic), builds a live digital twin, detects anomalies with ML, reasons about root causes with an LLM (Groq API), simulates fixes in a sandbox, and—after human approval—executes bounded, reversible adaptations.

### Core Goals (from the paper, reframed as project goals)

| # | Goal | One-liner |
|---|------|-----------|
| O1 | Connector Boundary | TwinOps never touches app internals; connectors are the only bridge |
| O2 | Application-Independent Twin | Typed graph twin with evidence tags (Measured / Predicted / Simulated) |
| O3 | ML + LLM Analysis | Isolation Forest anomaly detection + Groq LLM root-cause reasoning |
| O4 | Safe Adaptation | Bounded, reversible actions → simulation → human approval → rollback |
| O5 | Audit Trail | Every decision logged in the knowledge store |
| O6 | Dashboard | Real-time, evidence-tagged charts + approval UI |

---

## 2. System Architecture

### 2.1 High-Level Diagram

```
┌─────────────────────────────────────────────────────────────────────────┐
│                          TWINOPS PLATFORM                              │
│                                                                         │
│  ┌──────────┐   ┌──────────────┐   ┌───────────┐   ┌───────────────┐  │
│  │ Dashboard │   │  MAPE-K      │   │  Sandbox  │   │  Knowledge    │  │
│  │ (React +  │◄──│  Controller  │──►│  Simulator│   │  Store        │  │
│  │  Vite)    │   │              │   │           │   │  (In-Memory)  │  │
│  └─────▲─────┘   └──┬───┬──┬───┘   └───────────┘   └───────▲───────┘  │
│        │            │   │  │                                │          │
│        │      ┌─────▼┐ ┌▼──┴────┐                          │          │
│        │      │  ML  │ │  LLM   │                          │          │
│        │      │Engine│ │ Engine │                          │          │
│        │      │(sklearn)│(Groq API)                        │          │
│        │      └──────┘ └────────┘                          │          │
│        │                                                    │          │
│  ┌─────┴────────────────────────────────────────────────────┴───────┐  │
│  │                    TELEMETRY INGESTION BUS                       │  │
│  └─────────────────────────────▲───────────────────────────────────┘  │
│                                │                                      │
│  ┌─────────────────────────────┴───────────────────────────────────┐  │
│  │              UNIVERSAL CONNECTOR LAYER                          │  │
│  │  ┌────────────┐  ┌────────────┐  ┌────────────┐                │  │
│  │  │ OpenTelemetry│ │ Prometheus │  │  REST/HTTP │                │  │
│  │  │  Connector  │  │  Connector │  │  Connector │  ...           │  │
│  │  └──────┬─────┘  └──────┬─────┘  └──────┬─────┘                │  │
│  └─────────┼───────────────┼───────────────┼───────────────────────┘  │
│            │               │               │                          │
└────────────┼───────────────┼───────────────┼──────────────────────────┘
             │               │               │
     ════════╧═══════════════╧═══════════════╧════════════════
                    CONNECTOR BOUNDARY
     ═══════════════════════════════════════════════════════════
             │               │               │
      ┌──────▼──────┐ ┌─────▼──────┐ ┌──────▼──────┐
      │  Java App   │ │ Python App │ │  Node App   │
      │ (any stack) │ │ (any stack)│ │ (any stack) │
      └─────────────┘ └────────────┘ └─────────────┘
```

### 2.2 Data Flow & Guardrails

**Guardrail Principle**: Data flows in ONE direction through defined gates. No component can skip a gate.

```
Telemetry IN ──► Connector ──► Ingestion Bus ──► Twin State + Knowledge Store
                                                       │
                                                       ▼
                                              ┌─── Analyze (ML + LLM) ───┐
                                              │   Anomaly? Root Cause?    │
                                              └───────────┬───────────────┘
                                                          │ (advisory only)
                                                          ▼
                                              ┌─── Plan ──────────────────┐
                                              │  Bounded action proposal  │
                                              │  Validate against limits  │
                                              └───────────┬───────────────┘
                                                          │ (must pass bounds check)
                                                          ▼
                                              ┌─── Simulate ─────────────┐
                                              │  Run on twin, NOT live   │
                                              │  Must show improvement   │
                                              └───────────┬───────────────┘
                                                          │ (must pass θ_imp)
                                                          ▼
                                              ┌─── Human Approval ───────┐
                                              │  Dashboard gate          │
                                              └───────────┬───────────────┘
                                                          │ (explicit click)
                                                          ▼
                                              ┌─── Execute ──────────────┐
                                              │  Apply via connector     │
                                              │  Record rollback token   │
                                              └───────────┬───────────────┘
                                                          │
                                                          ▼
                                              ┌─── Observe & Rollback ───┐
                                              │  Measure post-change     │
                                              │  Auto-revert if worse    │
                                              └──────────────────────────┘
```

### 2.3 Guardrail Rules (Hard-Coded Safety)

| # | Guardrail | Enforcement |
|---|-----------|-------------|
| G1 | **LLM output is ADVISORY ONLY** — never auto-executes | Plan module validates; Execute module requires `approval_id` |
| G2 | **All adaptations are bounded** — `p_min ≤ p' ≤ p_max`, `|p' - p| ≤ δ_max` | Checked in Plan before simulation even starts |
| G3 | **Cooldown between changes** — `T_cool` seconds between executions | Execute module enforces; rejects if within window |
| G4 | **Simulation must show improvement** — `(R̂_cur - R̂_sim) / R̂_cur ≥ θ_imp` | Sandbox gate; fails closed (no action if simulation fails) |
| G5 | **Human must approve** — No auto-execution path exists | Dashboard presents; backend requires signed approval token |
| G6 | **Rollback is measurement-based** — auto-reverts if metrics degrade | Execute module checks post-change measurements |
| G7 | **Evidence tagging** — every value tagged as M (measured), P (predicted), S (simulated) | Enforced at data creation; tag is immutable |
| G8 | **Data stays in TwinOps** — telemetry does not leak to LLM beyond the prompt | LLM Engine builds a bounded evidence bundle; no raw dump |
| G9 | **Connector is the only exit** — no direct DB/SSH/process access to apps | Architecture: no other path exists in code |
| G10 | **Knowledge store is append-only for audit entries** — no deletion of decisions | API enforces; soft-delete only for cleanup |

---

## 3. Tech Stack

| Layer | Technology | Rationale |
|-------|-----------|-----------|
| **Frontend** | React 18 + TypeScript + Vite | Fast dev, type safety, modern tooling |
| **UI Styling** | Tailwind CSS | Utility-first, matches paper's prototype |
| **Charts** | Recharts or Apache ECharts | Evidence-tagged time-series visualization |
| **Backend** | Python 3.12+ (FastAPI) | ML/AI ecosystem, async-first, OpenAPI auto-docs |
| **LLM** | Groq API (Llama 3 / Mixtral) | Fast inference, cloud-hosted, no local GPU needed |
| **ML** | scikit-learn (Isolation Forest) + pandas + numpy | Paper's exact choice for anomaly detection |
| **In-Memory DB** | SQLite (`:memory:`) + optional Redis | Lightweight, zero-config; Redis for pub/sub if needed |
| **Connector Protocol** | OpenTelemetry (OTLP) + REST webhook | Industry standard, language-agnostic, any app can emit |
| **WebSocket** | FastAPI WebSockets | Real-time telemetry push to dashboard |
| **Packaging** | Docker Compose | Multi-container setup |

---

## 4. Connector Architecture (Deep Design)

The connector is the **most critical architectural decision**. It must work with **any** application regardless of language, framework, or library. This section covers what data we need, how we get it, the two-part connector design, and the full component-to-component flow.

---

### 4.1 Two-Part Connector Architecture

The connector has two sides separated by a standardized contract:

1. **Application-Side Adapter** — lives near/inside the monitored app, speaks its native language
2. **TwinOps-Side Connector Gateway** — receives, validates, normalizes, and routes everything

```
┌─────────────────────────────────────────────────────────────────┐
│                    MONITORED APPLICATIONS                       │
│                                                                 │
│  ┌──────────────┐   ┌──────────────┐   ┌──────────────┐        │
│  │  Spring Boot  │   │   Node.js    │   │   FastAPI    │        │
│  │  Application  │   │  Application │   │  Application │  ...   │
│  └──────┬───────┘   └──────┬───────┘   └──────┬───────┘        │
│         │                  │                   │                 │
│  ┌──────▼───────┐   ┌──────▼───────┐   ┌──────▼───────┐        │
│  │ SpringBoot   │   │   Node       │   │  FastAPI     │        │
│  │  Adapter     │   │  Adapter     │   │  Adapter     │        │
│  │              │   │              │   │              │        │
│  │ • Actuator   │   │ • prom-client│   │ • psutil     │        │
│  │ • Micrometer │   │ • pg.Pool    │   │ • SQLAlchemy │        │
│  │ • HikariCP   │   │ • express    │   │ • uvicorn    │        │
│  │   MXBean     │   │   middleware │   │   metrics    │        │
│  └──────┬───────┘   └──────┬───────┘   └──────┬───────┘        │
│         │                  │                   │                 │
│         └──────────────────┼───────────────────┘                │
│                            │                                    │
│            Standardized Connector Contract                      │
│            (Telemetry + Descriptor + Capabilities + Commands)   │
│                            │                                    │
└────────────────────────────┼────────────────────────────────────┘
                             │
         ════════════════════╧════════════════════
                   CONNECTOR BOUNDARY
         ════════════════════╤════════════════════
                             │
┌────────────────────────────┼────────────────────────────────────┐
│                            ▼                                    │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │              TWINOPS CONNECTOR GATEWAY                    │   │
│  │                                                          │   │
│  │  ┌─────────────┐  ┌───────────────┐  ┌───────────────┐  │   │
│  │  │ Validation  │  │ Authentication│  │ Normalization │  │   │
│  │  │ • Schema    │  │ • API keys    │  │ • Canonical   │  │   │
│  │  │   check     │  │ • HMAC verify │  │   mapping     │  │   │
│  │  │ • Required  │  │ • App identity│  │ • Unit        │  │   │
│  │  │   fields    │  │               │  │   conversion  │  │   │
│  │  └──────┬──────┘  └───────┬───────┘  └───────┬───────┘  │   │
│  │         └─────────────────┼───────────────────┘          │   │
│  │                           ▼                              │   │
│  │                    ┌─────────────┐                       │   │
│  │                    │   Routing   │                       │   │
│  │                    │ → Telemetry Bus                     │   │
│  │                    │ → Twin Sync                         │   │
│  │                    │ → Knowledge Store                   │   │
│  │                    └─────────────┘                       │   │
│  └──────────────────────────────────────────────────────────┘   │
│                                                                 │
│                 FRAMEWORK-INDEPENDENT TWINOPS CORE              │
└─────────────────────────────────────────────────────────────────┘
```

---

### 4.2 What Data We Need & Why

Before designing the contracts, we must know exactly what information flows through the connector and which TwinOps module consumes it.

#### 4.2.1 Required Data Inventory

| # | Information | Required? | Consumed By | Why |
|---|-------------|-----------|-------------|-----|
| 1 | Application ID and identity | **Yes** | All modules | Route telemetry to the correct twin instance |
| 2 | CPU utilization | **Yes** | Anomaly detection, Health score | Detect resource exhaustion |
| 3 | Memory / heap usage | **Yes** | Anomaly detection, Health score | Detect memory pressure |
| 4 | Request rate (req/s) | **Yes** | Anomaly detection, Forecasting | Detect traffic spikes, predict load |
| 5 | Response time (mean) | **Yes** | Anomaly detection, Forecasting, Simulation | Primary performance signal |
| 6 | Response time (p95) | **Yes** | Anomaly detection, Dashboard | Tail latency |
| 7 | Error rate | **Yes** | Anomaly detection, Rollback (Eq. 4) | Safety metric for rollback decisions |
| 8 | Application logs | **Yes** | LLM root-cause analysis | Context for reasoning |
| 9 | DB active connections | **If DB exists** | Pool analysis, Twin state | Pool saturation signal |
| 10 | DB idle connections | **If DB exists** | Pool analysis, Twin state | Pool capacity signal |
| 11 | DB pending connections | **If pool exists** | Pool analysis, Anomaly detection | Queuing pressure |
| 12 | DB max connections (configured) | **If pool exists** | Planning, Bounds check (Eq. 2) | Enforce `p_max` |
| 13 | Acquisition timeouts | **If pool exists** | Anomaly detection, LLM analysis | Direct failure signal |
| 14 | Component descriptions | **Yes** | Digital Twin graph | Build typed nodes |
| 15 | Component relationships | **Yes** | Digital Twin graph, LLM context | Build edges, dependency chain reasoning |
| 16 | Current configurations | **For adaptable components** | Planning, Simulation | Know current state before proposing changes |
| 17 | Available adaptation actions | **Yes** | Planner, Bounds check | Only propose what's permitted |
| 18 | Rollback capability | **For reversible actions** | Execute/Rollback | Must know if revert is possible |

#### 4.2.2 The Metric Availability Problem

**Not every application has every metric.** This is a critical design decision:

- Spring Boot uses HikariCP → has `pool_active`, `pool_idle`, `pool_pending`
- Node.js uses `pg.Pool` → has `pool_total`, `pool_idle`, `pool_waiting`
- FastAPI uses SQLAlchemy pool → has `pool_checkedout`, `pool_checkedin`
- A static site or pure compute service → **has no database at all**

**Reporting zero is WRONG** — zero would mean "the pool exists and has no pending connections," which is different from "there is no pool."

Every metric must declare its availability:

```json
{
  "metric": "db_pool_pending",
  "value": null,
  "available": false,
  "reason": "No connection pool configured"
}
```

**Schema rule**: Metrics are either `{ "value": <number>, "available": true }` or `{ "value": null, "available": false, "reason": "<why>" }`.

**Impact on TwinOps modules**:
- **Anomaly detection**: Only include available metrics in the feature vector
- **Health score**: Re-normalize weights excluding unavailable sub-scores
- **LLM analysis**: Evidence bundle states which metrics are unavailable
- **Planner**: Only propose actions for capabilities the app actually supports

---

### 4.3 Application Architecture Information (The Descriptor)

The paper calls this the **Application Descriptor**. It tells TwinOps the internal structure of an application so the Digital Twin can model it as a typed graph.

#### 4.3.1 Why the Twin Needs This

Suppose API response time increases. Without architecture knowledge, TwinOps only sees "response time is high." With the descriptor, the Digital Twin can trace the **dependency chain**:

```
API Gateway → Service Layer → Connection Pool → Database
     │              │               │              │
   slow?          slow?          saturated?      slow queries?
```

This gives the LLM the context to reason: *"Response time increased because the connection pool is saturated (45/50 active, 12 pending), causing service-layer requests to queue."*

#### 4.3.2 Descriptor Structure

Every adapter must provide this at registration time (and update it if the app's architecture changes):

```json
{
  "application_id": "cms-001",
  "name": "College Management System",
  "framework": "spring-boot",
  "version": "3.2.1",
  "components": [
    { "id": "web",     "type": "web_tier",         "technology": "Spring MVC" },
    { "id": "service", "type": "service_layer",     "technology": "Spring Service" },
    { "id": "pool",    "type": "connection_pool",   "technology": "HikariCP" },
    { "id": "db",      "type": "database",          "technology": "MySQL 8.0" }
  ],
  "relationships": [
    { "from": "web",     "relation": "calls",      "to": "service" },
    { "from": "service", "relation": "depends_on", "to": "pool" },
    { "from": "pool",    "relation": "uses",        "to": "db" }
  ],
  "current_config": {
    "pool": {
      "current_size": 10,
      "min_idle": 5,
      "max_size": 10,
      "connection_timeout_ms": 30000
    }
  }
}
```

**Supported component types**: `web_tier`, `service_layer`, `connection_pool`, `database`, `cache`, `message_queue`, `external_api`, `load_balancer`, `worker`

**Supported relationship types**: `calls`, `depends_on`, `uses`, `reads_from`, `writes_to`, `publishes_to`, `subscribes_to`

---

### 4.4 Adapter Interface Contract

Every application-side adapter must implement **four operations**. These map directly to the paper's four-part connector contract.

```python
# Base class — every adapter implements this
class ApplicationAdapter:
    """
    Application-side adapter interface.
    Each framework gets a concrete implementation.
    All must return the same canonical data structures.
    """

    def collect_telemetry(self) -> TelemetryPayload:
        """Contract (i): Collect metrics and logs, return canonical format."""
        raise NotImplementedError

    def get_application_descriptor(self) -> ApplicationDescriptor:
        """Contract (ii): Describe components and their relationships."""
        raise NotImplementedError

    def get_capabilities(self) -> CapabilityCatalog:
        """Contract (iii): List permitted adaptation actions with bounds."""
        raise NotImplementedError

    def execute_command(self, command: AdaptationCommand) -> CommandResult:
        """Contract (iv): Execute an approved action or rollback."""
        raise NotImplementedError
```

#### 4.4.1 Framework-Specific Implementations

```python
class SpringBootAdapter(ApplicationAdapter):
    """
    Collects from Spring Boot Actuator + Micrometer.
    Commands via HikariCP MXBean.
    """
    def collect_telemetry(self):
        # GET /actuator/metrics/hikaricp.connections.active
        # GET /actuator/metrics/jvm.memory.used
        # GET /actuator/metrics/http.server.requests
        # Parse Micrometer metrics → canonical TelemetryPayload
        ...

    def execute_command(self, command):
        # Call HikariCP MXBean to resize pool
        # Record previous size as rollback token
        ...


class NodeAdapter(ApplicationAdapter):
    """
    Collects from prom-client + pg.Pool stats.
    Commands via app's REST management endpoint.
    """
    def collect_telemetry(self):
        # GET /metrics (Prometheus format from prom-client)
        # Parse pg.Pool: { totalCount, idleCount, waitingCount }
        # Map to canonical schema
        ...


class FastAPIAdapter(ApplicationAdapter):
    """
    Collects from psutil + SQLAlchemy pool.
    Commands via app's REST management endpoint.
    """
    def collect_telemetry(self):
        # psutil.cpu_percent(), psutil.virtual_memory()
        # SQLAlchemy pool.status(): checkedout, checkedin, overflow
        # Map to canonical schema
        ...
```

**Key rule**: All three return **identical canonical data structures**. TwinOps never knows or cares which adapter produced the data.

---

### 4.5 The Four Standardized Contracts

These are the exact JSON structures that cross the connector boundary. The adapter produces them; the gateway consumes them.

#### Contract A: Telemetry Payload

```json
{
  "application_id": "cms-001",
  "timestamp": "2026-11-13T10:00:00Z",
  "metrics": {
    "cpu_usage": { "value": 45.2, "available": true },
    "heap_usage": { "value": 65.0, "available": true },
    "request_rate": { "value": 500, "available": true },
    "response_time_mean_ms": { "value": 1200, "available": true },
    "response_time_p95_ms": { "value": 2500, "available": true },
    "error_rate": { "value": 0.08, "available": true },
    "db_pool": {
      "active": { "value": 10, "available": true },
      "idle": { "value": 0, "available": true },
      "pending": { "value": 35, "available": true },
      "max": { "value": 10, "available": true },
      "acquisition_timeouts": { "value": 12, "available": true }
    }
  },
  "logs": [
    {
      "timestamp": "2026-11-13T09:59:58Z",
      "level": "WARN",
      "message": "HikariPool-1 - Connection is not available, request timed out after 30000ms",
      "source": "com.zaxxer.hikari.pool.HikariPool"
    }
  ]
}
```

For an app **without a database**:

```json
{
  "application_id": "static-api-002",
  "timestamp": "2026-11-13T10:00:00Z",
  "metrics": {
    "cpu_usage": { "value": 22.5, "available": true },
    "heap_usage": { "value": 40.0, "available": true },
    "request_rate": { "value": 1200, "available": true },
    "response_time_mean_ms": { "value": 45, "available": true },
    "response_time_p95_ms": { "value": 120, "available": true },
    "error_rate": { "value": 0.001, "available": true },
    "db_pool": {
      "active": { "value": null, "available": false, "reason": "No connection pool configured" },
      "idle": { "value": null, "available": false, "reason": "No connection pool configured" },
      "pending": { "value": null, "available": false, "reason": "No connection pool configured" },
      "max": { "value": null, "available": false, "reason": "No connection pool configured" },
      "acquisition_timeouts": { "value": null, "available": false, "reason": "No connection pool configured" }
    }
  },
  "logs": []
}
```

#### Contract B: Application Descriptor

```json
{
  "application_id": "cms-001",
  "name": "College Management System",
  "framework": "spring-boot",
  "version": "3.2.1",
  "components": [
    { "id": "web",     "type": "web_tier",       "technology": "Spring MVC" },
    { "id": "service", "type": "service_layer",   "technology": "Spring Service" },
    { "id": "pool",    "type": "connection_pool", "technology": "HikariCP" },
    { "id": "db",      "type": "database",        "technology": "MySQL 8.0" }
  ],
  "relationships": [
    { "from": "web",     "relation": "calls",      "to": "service" },
    { "from": "service", "relation": "depends_on", "to": "pool" },
    { "from": "pool",    "relation": "uses",        "to": "db" }
  ]
}
```

#### Contract C: Capability Catalog

```json
{
  "application_id": "cms-001",
  "actions": [
    {
      "action": "resize_connection_pool",
      "target": "pool",
      "constraints": {
        "min": 5,
        "max": 30,
        "max_delta": 5
      },
      "reversible": true,
      "requires_restart": false
    }
  ]
}
```

An app with **no adaptable components** returns an empty catalog:

```json
{
  "application_id": "static-api-002",
  "actions": []
}
```

The planner **must only consider actions listed here**. If `actions` is empty, TwinOps can still monitor and analyze but will never propose an adaptation.

#### Contract D: Command (TwinOps → App)

```json
{
  "command_id": "cmd-20261113-001",
  "application_id": "cms-001",
  "action": "resize_connection_pool",
  "target": "pool",
  "parameters": {
    "new_size": 15
  },
  "previous_value": {
    "size": 10
  },
  "approval_id": "approval-20261113-001",
  "idempotency_key": "idem-a1b2c3d4",
  "rollback_token": "rb-10-cms001-20261113"
}
```

**Command Result** (App → TwinOps):

```json
{
  "command_id": "cmd-20261113-001",
  "status": "applied",
  "applied_at": "2026-11-13T10:01:05Z",
  "actual_value": { "size": 15 },
  "rollback_token": "rb-10-cms001-20261113",
  "message": "Pool resized from 10 to 15"
}
```

---

### 4.6 Complete Data Flow: Component to Component

This shows exactly which data flows between which modules, end to end:

```
                        APPLICATION SIDE
                        ================

  ┌─────────────┐
  │ App Runtime  │──── Native metrics (Actuator, psutil, prom-client, etc.)
  └──────┬──────┘
         │
         ▼
  ┌─────────────┐
  │   Adapter   │──── collect_telemetry()      → Contract A (Telemetry)
  │             │──── get_descriptor()          → Contract B (Descriptor)
  │             │──── get_capabilities()        → Contract C (Capabilities)
  │             │◄─── execute_command(cmd)      ← Contract D (Command)
  └──────┬──────┘
         │  REST/HTTP (JSON)
         │
  ═══════╧═══════════════════════════════════════════════════════
                    CONNECTOR BOUNDARY
  ═══════╤═══════════════════════════════════════════════════════
         │
         ▼                 TWINOPS SIDE
                           ============

  ┌──────────────────────────────────────────────────────────┐
  │              CONNECTOR GATEWAY                            │
  │                                                          │
  │  1. Authenticate (API key / HMAC)                        │
  │  2. Validate schema (reject malformed payloads)          │
  │  3. Normalize (unit conversion, field mapping)           │
  │  4. Handle metric availability (null/unavailable)        │
  │  5. Route to internal modules:                           │
  └──────┬───────────┬───────────┬──────────┬────────────────┘
         │           │           │          │
         ▼           ▼           ▼          ▼
  ┌──────────┐ ┌──────────┐ ┌────────┐ ┌────────────┐
  │Telemetry │ │  Twin    │ │  ML    │ │ Knowledge  │
  │Ingestion │ │  Sync    │ │ Engine │ │ Store      │
  │          │ │          │ │        │ │ (append)   │
  │ • Store  │ │ • Update │ │ • IF   │ │ • Audit    │
  │   m_t    │ │   nodes  │ │ • Fore-│ │   trail    │
  │ • Health │ │ • Snap-  │ │   cast │ │            │
  │   score  │ │   shot   │ │        │ │            │
  └────┬─────┘ └────┬─────┘ └───┬────┘ └──────▲─────┘
       │            │           │              │
       └────────────┴───────────┘              │
                    │                          │
                    ▼                          │
            ┌──────────────┐                   │
            │  MAPE-K      │                   │
            │  Controller  │───────────────────┘
            │              │        (log every decision)
            │  Analyze ────┤
            │    ↓         │
            │  Plan ───────┤───► Bounds check (Eq. 2)
            │    ↓         │
            │  Simulate ───┤───► Improvement gate (Eq. 3)
            │    ↓         │
            │  Approval ───┤───► Human gate (Dashboard)
            │    ↓         │
            │  Execute ────┤───► Command → Connector → Adapter → App
            │    ↓         │
            │  Observe ────┤───► Rollback check (Eq. 4)
            └──────────────┘
                    │
                    ▼
            ┌──────────────┐
            │  LLM Engine  │
            │  (Groq API)  │
            │              │
            │ Receives:    │
            │ • Recent m_t │
            │ • ML flags   │
            │ • Log excerpt│
            │ • Twin slice │
            │ • Capability │
            │   catalog    │
            │              │
            │ Returns:     │
            │ • JSON only  │
            │ • Schema-    │
            │   validated  │
            │ • Advisory   │
            │   only       │
            └──────────────┘
```

### 4.7 Gateway Processing Pipeline

When the Connector Gateway receives data from an adapter, it runs this pipeline:

| Step | What | Failure Mode |
|------|------|-------------|
| 1. **Authenticate** | Verify API key or HMAC signature matches registered app | Reject with 401; log attempt |
| 2. **Validate Schema** | Check JSON against Contract A/B/C schema | Reject with 422; log malformed payload |
| 3. **Check App Registration** | Verify `application_id` is registered | Reject with 404; suggest registration |
| 4. **Handle Availability** | Process `available: false` metrics — exclude from feature vectors | Pass through; mark unavailable in twin |
| 5. **Normalize** | Convert units (e.g., bytes → MB, ns → ms), map framework-specific field names | Apply mapping rules from adapter config |
| 6. **Tag Evidence** | Stamp all values with `M` (Measured) tag + timestamp + source | Immutable after this point |
| 7. **Route** | Fan out to: Telemetry Store, Twin Sync, ML Engine, Knowledge Store | Each consumer processes independently |

### 4.8 How Each Framework Adapter Gets the Data

| Framework | CPU/Memory | Request Metrics | Pool Metrics | Logs | Commands |
|-----------|-----------|----------------|-------------|------|----------|
| **Spring Boot** | Actuator `/actuator/metrics/process.cpu.usage`, `/actuator/metrics/jvm.memory.used` | Micrometer `http.server.requests` | HikariCP MXBean: `hikaricp.connections.active`, `.idle`, `.pending`, `.max` | Logback via `/actuator/logfile` or log appender | HikariCP MXBean `setMaximumPoolSize()` |
| **Node.js** | `process.cpuUsage()`, `process.memoryUsage()` or `prom-client` | Express middleware counter/histogram | `pg.Pool`: `totalCount`, `idleCount`, `waitingCount` | Winston/Pino log transport | REST endpoint on app: `POST /admin/pool/resize` |
| **FastAPI** | `psutil.cpu_percent()`, `psutil.virtual_memory()` | Starlette middleware or Prometheus | SQLAlchemy `pool.status()`: `checkedout`, `checkedin`, `overflow` | Python `logging` handler | REST endpoint on app: `POST /admin/pool/resize` |
| **Go (net/http)** | `runtime.MemStats`, `/proc/stat` or OTel SDK | `promhttp` or OTel HTTP instrumentation | `database/sql.DBStats`: `InUse`, `Idle`, `WaitCount` | `slog` or `zap` | REST endpoint on app |
| **Generic / Legacy** | OTel SDK or push via OTLP HTTP POST | OTel SDK auto-instrumentation | Manual metric export if pool exists | Structured log push | REST endpoint on app |

### 4.9 Adapter Deployment Options

The adapter can run in three modes depending on the app's constraints:

| Mode | How | Best For |
|------|-----|----------|
| **Embedded Library** | Import adapter as a package in the app | Apps you own and can modify |
| **Sidecar Process** | Separate process alongside the app, polls its metrics endpoints | Apps you can't modify but can deploy alongside |
| **Agent (Zero-Code)** | OTel auto-instrumentation agent attached at startup | Java/Node/.NET apps where you only control startup flags |

---

## 5. Project Structure

```
c:\twinsops\
├── plan.md                          # ← This file
├── docker-compose.yml               # Full-stack orchestration
│
├── frontend/                        # React + TypeScript + Vite
│   ├── package.json
│   ├── vite.config.ts
│   ├── tsconfig.json
│   ├── tailwind.config.js
│   ├── index.html
│   └── src/
│       ├── main.tsx
│       ├── App.tsx
│       ├── api/                     # API client (axios/fetch)
│       │   ├── client.ts
│       │   ├── telemetry.ts
│       │   └── approvals.ts
│       ├── components/
│       │   ├── layout/
│       │   │   ├── Sidebar.tsx
│       │   │   ├── Header.tsx
│       │   │   └── MainLayout.tsx
│       │   ├── dashboard/
│       │   │   ├── HealthScore.tsx
│       │   │   ├── MetricsChart.tsx       # Evidence-tagged charts (M/P/S)
│       │   │   ├── TwinGraph.tsx          # Digital twin visualization
│       │   │   ├── AnomalyFeed.tsx
│       │   │   └── SystemOverview.tsx
│       │   ├── analysis/
│       │   │   ├── RootCauseCard.tsx       # LLM hypothesis display
│       │   │   ├── AnomalyDetails.tsx
│       │   │   └── EvidenceBundle.tsx
│       │   ├── approvals/
│       │   │   ├── ApprovalQueue.tsx       # Human approval gate UI
│       │   │   ├── ApprovalDetail.tsx
│       │   │   ├── SimulationResults.tsx
│       │   │   └── RollbackButton.tsx
│       │   ├── connectors/
│       │   │   ├── ConnectorList.tsx
│       │   │   ├── ConnectorRegistration.tsx
│       │   │   └── ConnectorStatus.tsx
│       │   └── knowledge/
│       │       ├── AuditLog.tsx
│       │       └── DecisionHistory.tsx
│       ├── hooks/
│       │   ├── useWebSocket.ts
│       │   ├── useTelemetry.ts
│       │   └── useApprovals.ts
│       ├── store/                   # Zustand state management
│       │   ├── telemetryStore.ts
│       │   ├── twinStore.ts
│       │   └── approvalStore.ts
│       ├── types/
│       │   ├── telemetry.ts
│       │   ├── twin.ts
│       │   ├── connector.ts
│       │   └── approval.ts
│       └── utils/
│           ├── evidenceTag.ts       # M / P / S tag helpers
│           └── formatters.ts
│
├── backend/                         # Python FastAPI
│   ├── pyproject.toml               # Dependencies (uv/pip)
│   ├── requirements.txt
│   ├── main.py                      # FastAPI app entry
│   ├── config.py                    # All tunable thresholds
│   │
│   ├── connector/                   # TwinOps-Side Connector Gateway
│   │   ├── __init__.py
│   │   ├── gateway.py               # Receive, authenticate, validate, route
│   │   ├── schema.py                # Canonical telemetry schema (Contracts A-D)
│   │   ├── descriptor.py            # App descriptor parser (Contract B)
│   │   ├── capability.py            # Capability catalog + bounds (Contract C)
│   │   ├── command.py               # Outbound command interface (Contract D)
│   │   ├── normalizer.py            # Unit conversion, field mapping
│   │   └── availability.py          # Handle metric availability (null/unavailable)
│   │
│   ├── telemetry/                   # Telemetry Monitoring (Monitor)
│   │   ├── __init__.py
│   │   ├── ingestion.py             # Receive + normalize telemetry
│   │   ├── health_score.py          # Weighted health score H_t (Eq. 1)
│   │   └── models.py               # Measurement vector m_t
│   │
│   ├── twin/                        # Application-Independent Digital Twin
│   │   ├── __init__.py
│   │   ├── graph.py                 # Typed graph (nodes, edges, state)
│   │   ├── sync.py                  # Twin ← telemetry synchronization
│   │   ├── snapshot.py              # Timestamped snapshots
│   │   └── evidence.py              # Evidence tag enforcement (M/P/S)
│   │
│   ├── ml/                          # ML Engine
│   │   ├── __init__.py
│   │   ├── anomaly.py               # Isolation Forest anomaly detection
│   │   ├── forecast.py              # Response time / pool saturation forecast
│   │   └── features.py              # Feature engineering from telemetry
│   │
│   ├── llm/                         # LLM Engine (Groq API)
│   │   ├── __init__.py
│   │   ├── client.py                # Groq API client wrapper
│   │   ├── prompts.py               # Prompt templates + evidence bundling
│   │   ├── schema.py                # JSON schema for LLM responses
│   │   └── validator.py             # Schema validation + sanitization
│   │
│   ├── controller/                  # MAPE-K Controller
│   │   ├── __init__.py
│   │   ├── loop.py                  # Main MAPE-K cycle (one per interval)
│   │   ├── analyze.py               # Combine ML + LLM signals
│   │   ├── plan.py                  # Propose bounded action (Eq. 2)
│   │   └── execute.py               # Apply + observe + rollback (Eq. 4)
│   │
│   ├── sandbox/                     # Sandbox Simulation
│   │   ├── __init__.py
│   │   ├── simulator.py             # Run candidate on twin state
│   │   ├── calibration.py           # R̂ = g(p, λ; φ) model fitting
│   │   └── validation.py            # Improvement gate (Eq. 3)
│   │
│   ├── knowledge/                   # Knowledge Store
│   │   ├── __init__.py
│   │   ├── store.py                 # In-memory store (SQLite :memory:)
│   │   ├── models.py                # Data models for all audit entries
│   │   └── queries.py               # Query helpers
│   │
│   ├── api/                         # REST + WebSocket API
│   │   ├── __init__.py
│   │   ├── routes/
│   │   │   ├── telemetry.py         # GET /api/telemetry/*
│   │   │   ├── twin.py              # GET /api/twin/*
│   │   │   ├── analysis.py          # GET /api/analysis/*
│   │   │   ├── approvals.py         # GET/POST /api/approvals/*
│   │   │   ├── connectors.py        # CRUD /api/connectors/*
│   │   │   ├── knowledge.py         # GET /api/knowledge/*
│   │   │   └── config.py            # GET/PUT /api/config/*
│   │   └── websocket.py             # WS /ws/telemetry, /ws/alerts
│   │
│   └── guardrails/                  # Safety Enforcement (cross-cutting)
│       ├── __init__.py
│       ├── bounds.py                # Eq. 2 bounds check
│       ├── cooldown.py              # T_cool enforcement
│       ├── approval_gate.py         # Require signed approval token
│       ├── rollback.py              # Measurement-based rollback (Eq. 4)
│       └── evidence_tag.py          # Immutable M/P/S tagging
│
├── adapters/                        # Application-Side Adapters
│   ├── base.py                      # ApplicationAdapter base class
│   ├── contracts.py                 # Pydantic models for Contracts A/B/C/D
│   ├── springboot/                  # Spring Boot adapter
│   │   ├── __init__.py
│   │   ├── adapter.py               # SpringBootAdapter implementation
│   │   ├── actuator.py              # Actuator + Micrometer metric collection
│   │   └── hikari.py                # HikariCP MXBean command execution
│   ├── node/                        # Node.js adapter
│   │   ├── __init__.py
│   │   ├── adapter.py               # NodeAdapter implementation
│   │   └── pg_pool.py               # pg.Pool metric mapping
│   ├── fastapi_adapter/             # FastAPI adapter
│   │   ├── __init__.py
│   │   ├── adapter.py               # FastAPIAdapter implementation
│   │   └── sqlalchemy_pool.py       # SQLAlchemy pool metric mapping
│   └── generic/                     # Generic REST/OTLP push adapter
│       ├── __init__.py
│       └── adapter.py               # GenericAdapter for any app
│
└── demo/                           # Demo monitored application
    ├── docker-compose.yml
    ├── app.py                       # Simple FastAPI app (simulates a target)
    ├── adapter_setup.py             # FastAPIAdapter wired to demo app
    ├── fault_injection.py           # Endpoints to inject faults (slow queries, CPU stress, etc.)
    └── connector_descriptor.json    # App descriptor for TwinOps registration
```

---

## 6. Implementation Phases

### Phase 1 — Foundation (Week 1–2)

**Goal**: Backend skeleton + connector + telemetry ingestion + in-memory store

| Task | Module | Details |
|------|--------|---------|
| 1.1 | Project Setup | Init Vite (React+TS), FastAPI, Docker Compose, monorepo structure |
| 1.2 | Config System | `config.py` — all thresholds, bounds, weights as a Pydantic settings class |
| 1.3 | In-Memory DB | SQLite `:memory:` with SQLAlchemy models for telemetry, twin state, audit log |
| 1.4 | Canonical Schema | Define the telemetry schema: CPU, memory, request rate, response time (mean, p95), error rate, pool metrics |
| 1.5 | Connector Gateway | OTLP HTTP receiver endpoint (parse OTel metrics/traces/logs) |
| 1.6 | App Descriptor | REST endpoint `POST /api/connectors/register` — accepts descriptor JSON |
| 1.7 | Telemetry Ingestion | Normalize incoming OTel data → canonical schema → store |
| 1.8 | Health Score | Implement weighted health score `H_t` (Eq. 1) |
| 1.9 | REST API scaffold | FastAPI routes for telemetry, connectors, config |
| 1.10 | Demo App | Simple Python app with OTel auto-instrumentation that generates metrics |

**Deliverable**: Telemetry flows from demo app → connector → TwinOps → stored in memory.

---

### Phase 2 — Digital Twin + ML (Week 3–4)

**Goal**: Twin graph, anomaly detection, forecasting

| Task | Module | Details |
|------|--------|---------|
| 2.1 | Twin Graph | Typed graph data structure: nodes (components), edges (relations), timestamped state |
| 2.2 | Twin Sync | Telemetry → update twin node states each interval |
| 2.3 | Twin Snapshots | Periodic snapshots, configurable retention, age-based expiry |
| 2.4 | Evidence Tagging | Enforce M/P/S tags at creation; immutable through API |
| 2.5 | Anomaly Detection | Isolation Forest on sliding window of measurement vectors |
| 2.6 | Rule-Based Detection | Configurable threshold checks per metric |
| 2.7 | Combined Detection | Anomaly raised when rule fires OR `a_t ≥ τ` for `n` consecutive intervals |
| 2.8 | Forecasting | Predict response time + pool saturation `h` intervals ahead (linear + tree models) |
| 2.9 | Feature Engineering | Build feature matrix from telemetry for ML models |
| 2.10 | Twin API | REST endpoints for twin state, snapshots, graph query |

**Deliverable**: Twin mirrors app state; anomalies detected and tagged; forecasts generated.

---

### Phase 3 — LLM Engine + MAPE-K Controller (Week 5–6)

**Goal**: Groq LLM integration, full MAPE-K loop, sandbox simulation

| Task | Module | Details |
|------|--------|---------|
| 3.1 | Groq Client | API wrapper with retry, rate limiting, error handling |
| 3.2 | Evidence Bundle Builder | Assemble bounded context: recent measurements, ML flags, log excerpts, twin slice, capability catalog |
| 3.3 | Prompt Templates | Structured prompts for root-cause analysis; enforce JSON schema output |
| 3.4 | Schema Validation | Validate LLM JSON response; discard + log if malformed or out-of-catalog |
| 3.5 | Analyze Step | On anomaly → build evidence bundle → call Groq → validate response → store |
| 3.6 | Plan Step | Reconcile ML + LLM signals → propose action → enforce bounds (Eq. 2) + cooldown |
| 3.7 | Sandbox Simulator | Calibrated model `R̂ = g(p, λ; φ)` fitted on twin history |
| 3.8 | Validation Gate | Improvement check (Eq. 3): response time, error rate, connection cap |
| 3.9 | MAPE-K Loop | One cycle per observation interval: Monitor → Analyze → Plan → Simulate → gate |
| 3.10 | Approval Queue | Store pending approvals; expose via API |

**Deliverable**: Full analysis pipeline: anomaly → LLM reasoning → bounded proposal → simulation → approval queue.

---

### Phase 4 — Execution + Rollback + Knowledge Store (Week 7–8)

**Goal**: Human approval, execution via connector, measurement-based rollback, audit trail

| Task | Module | Details |
|------|--------|---------|
| 4.1 | Approval API | `POST /api/approvals/{id}/approve` — signed token, records who/when |
| 4.2 | Command Interface | Outbound REST call to app's adaptation endpoint with rollback token |
| 4.3 | Post-Change Observation | Monitor for `T_obs` window after execution |
| 4.4 | Auto-Rollback | Revert if `(R̄_post - R̄_pre)/R̄_pre > θ_rb` or `Ē_post > Ē_pre + ε_rb` (Eq. 4) |
| 4.5 | Manual Rollback | Dashboard button → immediate revert via connector |
| 4.6 | Simulation Fidelity | Compute `e_sim` (Eq. 5) after each applied change; track accuracy |
| 4.7 | Knowledge Store | Append-only audit: telemetry history, twin snapshots, anomalies, analyses, approvals, actions, rollbacks |
| 4.8 | Decision History API | Query audit trail with filters |
| 4.9 | Guardrail Tests | Integration tests verifying all 10 guardrails |
| 4.10 | WebSocket Alerts | Real-time push for anomalies, approvals, executions, rollbacks |

**Deliverable**: End-to-end loop works: detect → analyze → propose → simulate → approve → execute → observe → rollback.

---

### Phase 5 — Frontend Dashboard (Week 9–11)

**Goal**: Full React dashboard with real-time updates

| Task | Module | Details |
|------|--------|---------|
| 5.1 | Layout Shell | Sidebar nav, header, main content area (Tailwind) |
| 5.2 | System Overview | Connected apps, health scores, connector status |
| 5.3 | Health Score Widget | Real-time H_t gauge with trend |
| 5.4 | Metrics Charts | Time-series with evidence tags: solid line (M), dashed (P), dotted (S) |
| 5.5 | Twin Graph Viz | Interactive graph of app components + state (D3 or react-flow) |
| 5.6 | Anomaly Feed | Live feed of detected anomalies with severity |
| 5.7 | Root Cause Cards | LLM hypothesis display with evidence citations |
| 5.8 | Approval Queue UI | List of pending actions with simulation results, approve/reject buttons |
| 5.9 | Simulation Detail | Show simulated vs current metrics, improvement percentage |
| 5.10 | Rollback Controls | One-click manual rollback with confirmation |
| 5.11 | Audit Log View | Searchable, filterable decision history |
| 5.12 | Connector Management | Register new apps, view status, test connectivity |
| 5.13 | Config Panel | Tune thresholds, weights, bounds from the UI |
| 5.14 | WebSocket Integration | Real-time updates across all views |

**Deliverable**: Fully functional dashboard with real-time monitoring, approval workflow, and audit trail.

---

### Phase 6 — Integration, Testing & Demo (Week 12–13)

**Goal**: End-to-end testing, demo scenarios, documentation

| Task | Module | Details |
|------|--------|---------|
| 6.1 | Demo App Enhancement | Add fault injection endpoints (slow queries, CPU stress, pool exhaustion) |
| 6.2 | Scenario Testing | Run paper's scenarios: normal, traffic ramp, pool exhaustion, slow queries, CPU stress, memory pressure, error injection, non-pool fault (negative control) |
| 6.3 | Multi-App Demo | Connect a second demo app (Node.js) to prove connector universality |
| 6.4 | Docker Compose | Full stack: frontend + backend + demo apps, single `docker compose up` |
| 6.5 | Performance Testing | Verify TwinOps overhead (CPU/memory) is reasonable |
| 6.6 | Documentation | README, API docs (auto-generated from FastAPI), architecture guide |
| 6.7 | Presentation Prep | Screenshots, demo script, recorded walkthrough |

**Deliverable**: Working demo with fault injection, multi-app support, and presentation-ready materials.

---

## 7. API Design (Key Endpoints)

### 7.1 Connector APIs

```
POST   /api/connectors/register          # Register new app (descriptor + capabilities)
GET    /api/connectors                    # List connected apps
GET    /api/connectors/{id}/status        # Connection health
DELETE /api/connectors/{id}               # Unregister
```

### 7.2 Telemetry APIs

```
POST   /api/telemetry/ingest             # OTLP-compatible telemetry receiver
GET    /api/telemetry/latest             # Current measurement vector
GET    /api/telemetry/history?from=&to=  # Historical data
GET    /api/telemetry/health             # Current health score
WS     /ws/telemetry                     # Real-time telemetry stream
```

### 7.3 Twin APIs

```
GET    /api/twin/state                   # Current twin graph
GET    /api/twin/snapshots               # Historical snapshots
GET    /api/twin/components              # Component list
```

### 7.4 Analysis APIs

```
GET    /api/analysis/anomalies           # Detected anomalies
GET    /api/analysis/anomalies/{id}      # Anomaly detail + evidence bundle
GET    /api/analysis/forecasts           # Current predictions
GET    /api/analysis/root-causes         # LLM hypotheses
WS     /ws/alerts                        # Real-time anomaly alerts
```

### 7.5 Approval APIs

```
GET    /api/approvals                    # Pending approvals
GET    /api/approvals/{id}               # Detail: simulation results, evidence
POST   /api/approvals/{id}/approve       # Approve (requires auth)
POST   /api/approvals/{id}/reject        # Reject with reason
POST   /api/approvals/{id}/rollback      # Manual rollback
```

### 7.6 Knowledge Store APIs

```
GET    /api/knowledge/audit              # Audit trail (paginated)
GET    /api/knowledge/decisions          # Decision history
GET    /api/knowledge/stats              # Simulation fidelity stats
```

### 7.7 Config APIs

```
GET    /api/config                       # Current configuration
PUT    /api/config                       # Update thresholds/weights/bounds
```

---

## 8. Configurable Parameters (from Paper)

```python
# config.py — All tunable values in one place

class TwinOpsConfig(BaseSettings):
    # Health Score (Eq. 1)
    health_weights: dict = {
        "cpu": 0.15, "memory": 0.15, "response_time": 0.25,
        "error_rate": 0.20, "pool_saturation": 0.25
    }

    # Anomaly Detection
    anomaly_threshold_tau: float = 0.7        # Isolation Forest threshold
    consecutive_intervals_n: int = 3          # Consecutive anomalies required
    detection_window_size: int = 50           # Sliding window for IF

    # Rule-Based Thresholds
    cpu_threshold: float = 0.85
    memory_threshold: float = 0.90
    response_time_p95_ms: float = 2000
    error_rate_threshold: float = 0.05
    pool_saturation_threshold: float = 0.85

    # Adaptation Bounds (Eq. 2)
    pool_min: int = 5
    pool_max: int = 50
    pool_delta_max: int = 10
    cooldown_seconds: int = 300               # T_cool

    # Simulation Validation (Eq. 3)
    improvement_threshold: float = 0.10       # θ_imp (10% improvement required)
    error_rate_epsilon: float = 0.01          # ε
    max_db_connections: int = 100             # D̂_max

    # Rollback (Eq. 4)
    rollback_response_threshold: float = 0.15  # θ_rb
    rollback_error_epsilon: float = 0.02       # ε_rb
    observation_window_seconds: int = 120      # T_obs

    # Forecasting
    forecast_horizon_h: int = 5               # intervals ahead

    # MAPE-K
    observation_interval_seconds: int = 30    # seconds between cycles

    # Twin
    snapshot_retention_minutes: int = 60      # max snapshot age for simulation

    # Groq LLM
    groq_api_key: str = ""
    groq_model: str = "llama-3.3-70b-versatile"
    llm_max_evidence_tokens: int = 4000       # G8: bounded evidence bundle
    llm_temperature: float = 0.1              # low for deterministic analysis
```

---

## 9. Key Implementation Details

### 9.1 Evidence Tagging (Guardrail G7)

```python
from enum import Enum
from pydantic import BaseModel
from datetime import datetime

class EvidenceTag(str, Enum):
    MEASURED = "M"       # Ground truth from telemetry
    PREDICTED = "P"      # ML model output
    SIMULATED = "S"      # Sandbox simulation estimate

class TaggedValue(BaseModel):
    value: float
    tag: EvidenceTag          # Immutable after creation
    timestamp: datetime
    source: str               # Which module produced it
    confidence: float | None  # Optional for P and S
```

### 9.2 LLM Response Schema (Guardrail G1)

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "type": "object",
  "required": ["observed_problem", "root_cause", "recommended_action"],
  "properties": {
    "observed_problem": {
      "type": "string",
      "maxLength": 500
    },
    "root_cause": {
      "type": "object",
      "required": ["hypothesis", "evidence_refs"],
      "properties": {
        "hypothesis": { "type": "string", "maxLength": 1000 },
        "evidence_refs": {
          "type": "array",
          "items": { "type": "string" }
        }
      }
    },
    "recommended_action": {
      "type": "object",
      "required": ["action_type"],
      "properties": {
        "action_type": {
          "type": "string",
          "enum": ["pool_resize", "no_action", "manual_investigation"]
        },
        "target_value": { "type": "integer" },
        "reasoning": { "type": "string", "maxLength": 500 }
      }
    },
    "confidence": {
      "type": "number", "minimum": 0, "maximum": 1
    }
  },
  "additionalProperties": false
}
```

### 9.3 MAPE-K Cycle (Core Loop)

```python
async def mape_k_cycle(ctx: CycleContext):
    # MONITOR — ingest telemetry, sync twin, compute health
    measurement = await monitor(ctx)

    # ANALYZE — ML anomaly detection + LLM root cause (if anomaly)
    analysis = await analyze(ctx, measurement)
    if not analysis.anomaly_detected:
        return  # No action needed

    # PLAN — propose bounded action (or advisory-only)
    proposal = await plan(ctx, analysis)
    if proposal is None or not proposal.is_executable:
        await store_advisory(ctx, analysis)
        return

    # SIMULATE — validate on twin
    sim_result = await simulate(ctx, proposal)
    if not sim_result.passes_gate:
        await store_rejected(ctx, proposal, sim_result)
        return

    # GATE — queue for human approval
    await queue_for_approval(ctx, proposal, sim_result, analysis)
    # Execution happens when human approves via dashboard
```

---

## 10. Risk Mitigation

| Risk | Mitigation |
|------|-----------|
| Groq API rate limits | Implement request queue, exponential backoff, cache repeated analyses |
| LLM hallucination | Strict JSON schema validation; discard invalid; never auto-execute |
| In-memory data loss | Periodic checkpoint to SQLite file; configurable persistence interval |
| Connector security | HMAC-signed commands; capability-bounded; no arbitrary execution |
| Anomaly false positives | Require `n` consecutive intervals; tunable thresholds; human in the loop |
| Demo realism | Fault injection endpoints that simulate real-world patterns |

---

## 11. Definition of Done (per Phase)

- [ ] **Phase 1**: `docker compose up` → demo app sends OTel metrics → TwinOps ingests → health score visible via API
- [ ] **Phase 2**: Twin graph updates in real-time; Isolation Forest detects injected anomalies; forecasts generated
- [ ] **Phase 3**: Anomaly triggers Groq analysis → valid JSON hypothesis → bounded proposal → simulation → approval queue
- [ ] **Phase 4**: Approve in API → connector applies change → observe → auto-rollback if degraded → audit logged
- [ ] **Phase 5**: Dashboard shows all the above in real-time with evidence tags and approval workflow
- [ ] **Phase 6**: Full demo with fault injection, multi-app connector, Docker packaging, documentation

---

## 12. Timeline Summary

```
Week  1-2   ████████░░░░░░░░░░░░░░░░░░  Phase 1: Foundation
Week  3-4   ░░░░░░░░████████░░░░░░░░░░  Phase 2: Twin + ML
Week  5-6   ░░░░░░░░░░░░░░░░████████░░  Phase 3: LLM + MAPE-K
Week  7-8   ░░░░░░░░░░░░░░░░░░░░████░░  Phase 4: Execute + Rollback
Week  9-11  ░░░░░░░░░░░░░░░░░░░░░░████  Phase 5: Dashboard
Week 12-13  ░░░░░░░░░░░░░░░░░░░░░░░░██  Phase 6: Integration + Demo
```

> **Parallel tip**: Frontend (Phase 5) can start in Week 5 alongside Phase 3 using mock APIs. This compresses the timeline to ~10 weeks.

