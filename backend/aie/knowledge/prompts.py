"""Prompt templates for component knowledge extraction via LLM."""

from __future__ import annotations

COMPONENT_ANALYSIS_PROMPT = """You are an expert code intelligence and software architecture analyst. Deeply analyse the provided source code, configurations, and documentation to reconstruct exactly how the component works internally.

Your goal is to produce a technical knowledge representation detailed enough that an AI debugger can understand and diagnose the component without accessing its source code.

**Analyse the actual implementation, not just its purpose.**

Capture:
- Internal modules, classes, functions, call chains, and dependencies.
- Data structures: exact types, initialization, capacity, configuration, stored data, operations, algorithms, and complexity where determinable.
- Complete execution flows: entry point → request/event handling → validation → authentication/authorization → function calls → data structure operations → database/API/tool interactions → response.
- Database interactions: client initialization, connection pools, authentication mechanism, queries, transactions, timeouts, retries, and cleanup.
- Algorithms and business logic: exact steps, conditions, branching, inputs, outputs, and edge cases.
- State management: initialization, mutations, lifecycle, persistence, and concurrency.
- Configuration parameters: actual values, defaults, source files, and line numbers when available.
- Component dependencies and communication: data exchanged, direction, and synchronous/asynchronous behaviour.
- Failure scenarios: root causes, affected code paths, symptoms, diagnostic checks, and mitigations.
- Monitoring requirements: relevant metrics, their purpose, and whether they are implemented or recommended.

**Important instructions:**
1. Trace execution across relevant files instead of analysing functions in isolation.
2. Explain how implementation details interact. Do not merely list symbols or libraries.
3. Be precise. For example, if a Bloom filter exists, explain its initialization, capacity, hash functions, false-positive probability, insertion, and lookup behaviour only to the extent established by the code.
4. Identify exact data flow, control flow, state transitions, and resource lifecycle.
5. Include meaningful implementation details even when they exist in helper functions or external modules.
6. Every significant claim must have source-file and line references when available.
7. Never invent missing implementation details, configuration values, metrics, or execution paths.
8. Label findings as `code_verified`, `configuration_verified`, `documentation_claim`, `inferred`, or `unknown`.
9. Distinguish existing monitoring from recommended monitoring.
10. Never expose credentials, secrets, tokens, or passwords.
11. If required source files are missing, explicitly identify the knowledge gaps.

**Output:** Return strictly valid JSON conforming to the supplied schema. Be technically detailed but avoid repetition and irrelevant explanations. Prioritize implementation-level knowledge that helps an AI debugger reconstruct execution and identify root causes without reading the original code.

### COMPONENT OVERVIEW
- Name: {component_name}
- Type: {component_type}
- Technology: {technology}

### RELATED COMPONENTS
{related_components_summary}

### SOURCE CODE EXCERPTS
{source_code_excerpts}

### CONFIGURATION EXCERPTS
{configuration_excerpts}

### DOCUMENTATION EXCERPTS
{documentation_excerpts}

### REQUIRED OUTPUT JSON SCHEMA
{json_schema}

Provide your analysis in strictly valid JSON conforming to the schema:
"""
