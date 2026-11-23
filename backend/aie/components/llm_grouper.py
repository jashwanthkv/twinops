"""
aie/components/llm_grouper.py — LLM-first architecture component grouper.

Strategy:
  1. Collect all files from CodebaseIR with their imports, class names, function names.
  2. Build a compact, token-safe codebase sketch (batched for large repos).
  3. Single Groq call: "Given these files and their symbols, group them into named
     system architecture components. Each component must have a name, kind,
     responsibility, technologies, and the file list that belongs to it."
  4. Convert returned groups → DiscoveredComponent objects.
  5. Fall back to the legacy rule engine if LLM is unavailable or fails.

Batching strategy for large repos (>150 files):
  - Sort files by path, batch into chunks of 150.
  - Run one LLM call per batch, merge results, deduplicate by name.
"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

logger = logging.getLogger(__name__)

# ── Prompt ──────────────────────────────────────────────────────────────────

_SYSTEM_PROMPT = """\
You are an expert software architect. Your job is to analyze a codebase and \
group its files into logical system architecture components.

Rules:
- Look at file paths, imports, class names, and function names.
- Group files that belong to the SAME architectural concern together.
- Do NOT create a catch-all "Domain Business Logic" group unless absolutely needed.
- Use SPECIFIC names like "Rate Limiter", "JWT Authentication", "Redis Cache", \
"User Service", "API Gateway", "Background Worker", "Database Layer", etc.
- Each component must have REAL evidence from the file list.
- Return ONLY valid JSON — no markdown, no prose, no code fences.\
"""

_USER_PROMPT = """\
Here is the codebase file sketch for the app. Group these files into \
system architecture components.

CODEBASE SKETCH:
{sketch}

Return a JSON array where each element is:
{{
  "name": "Short, specific component name (e.g. Rate Limiter, JWT Auth, Redis Cache)",
  "kind": "one of: api_routing | auth | rate_limiter | middleware | cache | \
database | domain_service | background_worker | vector_store | ai_agent | \
message_queue | frontend | ingress | unknown",
  "layer": "one of: edge | security | application | integration | data | \
messaging | ai | observability | platform | presentation | other",
  "responsibility": "One sentence: what this component does in the system.",
  "technologies": ["list", "of", "tech", "names", "detected"],
  "files": ["exact/file/path.py", "as/given/above.py"],
  "confidence": 0.0
}}

Important:
- Every file in the sketch must appear in exactly ONE component.
- Files can share membership if they are tightly coupled.
- Confidence: 0.9 if clear signal from imports/names, 0.7 if inferred from path, \
0.5 if uncertain.
- technologies: use package names from imports (e.g. "fastapi", "redis", "sqlalchemy").
"""

_MAX_FILES_PER_BATCH = 150
_GROQ_MODEL = "llama-3.3-70b-versatile"


# ── File sketch builder ──────────────────────────────────────────────────────

def _build_file_sketch(files: list[Any]) -> str:
    """
    Build a compact text sketch of each file: path + top-level symbols.
    Keeps tokens manageable.
    """
    lines: list[str] = []
    for f in files:
        path = getattr(f, "file_path", "")
        if not path:
            continue

        # Collect unique root import modules
        imports: set[str] = set()
        for imp in getattr(f, "imports", []):
            mod = (getattr(imp, "module", None) or getattr(imp, "from_module", None) or "").strip()
            if mod:
                root = mod.split(".")[0]
                if root and not root.startswith("_"):
                    imports.add(root)

        # Class names
        classes = [getattr(c, "name", "") for c in getattr(f, "classes", [])]

        # Function names (top-level, non-dunder)
        functions = [
            getattr(fn, "name", "")
            for fn in getattr(f, "functions", [])
            if not getattr(fn, "name", "").startswith("__")
        ][:8]  # cap at 8 to save tokens

        parts: list[str] = [f"FILE: {path}"]
        if imports:
            parts.append(f"  imports: {', '.join(sorted(imports))}")
        if classes:
            parts.append(f"  classes: {', '.join(classes)}")
        if functions:
            parts.append(f"  functions: {', '.join(functions)}")
        lines.append("\n".join(parts))

    return "\n\n".join(lines)


# ── Groq call ───────────────────────────────────────────────────────────────

def _call_groq(sketch: str, api_key: str) -> list[dict] | None:
    """Single Groq call → list of component dicts. Returns None on failure."""
    try:
        from groq import Groq  # type: ignore

        client = Groq(api_key=api_key)
        resp = client.chat.completions.create(
            model=os.environ.get("GROQ_MODEL", _GROQ_MODEL),
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": _USER_PROMPT.format(sketch=sketch)},
            ],
            temperature=0,
            max_tokens=4096,
        )
        raw = resp.choices[0].message.content.strip()

        # Strip markdown code fences if present
        raw = re.sub(r"^```[a-zA-Z]*\n?", "", raw)
        raw = re.sub(r"\n?```$", "", raw)
        raw = raw.strip()

        result = json.loads(raw)
        if isinstance(result, list):
            return result
        # Sometimes LLM wraps in {"components": [...]}
        if isinstance(result, dict):
            for key in ("components", "groups", "architecture"):
                if isinstance(result.get(key), list):
                    return result[key]
        return None

    except Exception as e:
        logger.warning("[LLMGrouper] Groq call failed: %s", e)
        return None


# ── Deduplication & merge ────────────────────────────────────────────────────

def _merge_batches(batches: list[list[dict]]) -> list[dict]:
    """
    Merge component lists from multiple batches.
    Components with the same `kind` get merged (files combined).
    Others are kept separate.
    """
    merged: dict[str, dict] = {}  # key: normalized name
    for batch in batches:
        for comp in batch:
            name = comp.get("name", "Unknown").strip()
            kind = comp.get("kind", "unknown")
            norm_key = f"{kind}::{name.lower()}"
            if norm_key in merged:
                # Merge files
                existing = merged[norm_key]
                existing_files = set(existing.get("files", []))
                new_files = set(comp.get("files", []))
                existing["files"] = sorted(existing_files | new_files)
                # Merge technologies
                existing_techs = set(existing.get("technologies", []))
                new_techs = set(comp.get("technologies", []))
                existing["technologies"] = sorted(existing_techs | new_techs)
            else:
                merged[norm_key] = dict(comp)

    return list(merged.values())


# ── Main entry point ─────────────────────────────────────────────────────────

def group_by_llm(
    codebase_ir: Any,
    profile: Any = None,
    api_key: str | None = None,
) -> list[dict] | None:
    """
    Main entry: takes CodebaseIR, returns list of component dicts or None.

    Returns None if LLM is unavailable so caller can fall back to rule engine.

    Each dict has keys:
      name, kind, layer, responsibility, technologies, files, confidence
    """
    # Resolve API key from multiple sources
    if not api_key:
        for env_name in ["GROQ_API_KEY", "AIE_GROQ_API_KEY", "GROQ_API_KEY1"]:
            api_key = os.environ.get(env_name, "").strip()
            if api_key:
                break

    if not api_key:
        logger.info("[LLMGrouper] No Groq API key — skipping LLM grouping")
        return None

    if codebase_ir is None:
        return None

    all_files = getattr(codebase_ir, "files", [])
    # Filter to source files only (skip binary, test files if too many)
    source_files = [
        f for f in all_files
        if not getattr(f, "parse_error", False)
        and getattr(f, "file_path", "").endswith(
            (".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".java", ".cs", ".rb", ".php")
        )
    ]

    if not source_files:
        logger.info("[LLMGrouper] No source files to group")
        return None

    logger.info("[LLMGrouper] Grouping %d source files via LLM", len(source_files))

    # Batch if large repo
    batches: list[list[dict]] = []
    for i in range(0, len(source_files), _MAX_FILES_PER_BATCH):
        batch_files = source_files[i: i + _MAX_FILES_PER_BATCH]
        sketch = _build_file_sketch(batch_files)

        logger.info(
            "[LLMGrouper] Calling Groq for batch %d/%d (%d files)",
            (i // _MAX_FILES_PER_BATCH) + 1,
            (len(source_files) + _MAX_FILES_PER_BATCH - 1) // _MAX_FILES_PER_BATCH,
            len(batch_files),
        )
        result = _call_groq(sketch, api_key)
        if result:
            batches.append(result)
        else:
            logger.warning("[LLMGrouper] Batch %d returned no result", i // _MAX_FILES_PER_BATCH + 1)

    if not batches:
        logger.warning("[LLMGrouper] All Groq batches failed")
        return None

    merged = _merge_batches(batches)

    # Validate: every component needs a name and at least one file
    valid = [
        c for c in merged
        if c.get("name") and c.get("files")
    ]

    logger.info("[LLMGrouper] LLM returned %d valid component groups", len(valid))
    return valid if valid else None
