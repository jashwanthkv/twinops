"""Map-reduce LLM synthesizer for architecture component discovery.

MAP phase: One LLM call per ArchCandidate cluster -> structured JSON.
REDUCE phase: One LLM call with all mapped components -> merged, deduplicated output.

All claims must cite evidence IDs. The verifier (verifier.py) drops anything unprovable.
Temperature: 0 (deterministic). JSON schema constrained.
LLM-down fallback: returns candidates as-is with lower confidence.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

from aie.components.candidates import ArchCandidate

logger = logging.getLogger(__name__)

_MAP_PROMPT = """\
You are an expert software architect. Analyze this candidate architectural component \
and return ONLY valid JSON (no prose, no markdown).

Candidate:
  name: {name}
  kind: {kind}
  layer: {layer}
  files: {files}
  technologies: {technologies}

Return JSON with schema:
{{
  "name": "...",
  "layer": "edge|security|application|integration|data|messaging|ai|observability|platform|presentation|other",
  "kind": "...",
  "responsibility": "one-sentence description of what this does",
  "technologies": ["..."],
  "confidence": 0.0-1.0
}}
"""

_REDUCE_PROMPT = """\
You are an expert software architect. Given these discovered components from one repository, \
merge duplicates and add a summary. Return ONLY valid JSON.

Components:
{components_json}

Return JSON:
{{
  "components": [
    {{
      "name": "...",
      "layer": "...",
      "kind": "...",
      "responsibility": "...",
      "technologies": ["..."],
      "confidence": 0.0-1.0
    }}
  ],
  "request_flow_summary": "one-sentence description of request path"
}}
"""


def _call_groq(prompt: str) -> dict[str, Any] | None:
    """Call Groq API. Returns parsed JSON or None on failure."""
    api_key = os.environ.get("GROQ_API_KEY", "")
    if not api_key:
        return None
    try:
        from groq import Groq  # type: ignore
        client = Groq(api_key=api_key)
        resp = client.chat.completions.create(
            model=os.environ.get("GROQ_MODEL", "llama-3.1-8b-instant"),
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            max_tokens=1024,
        )
        text = resp.choices[0].message.content.strip()
        # Strip markdown fences if present
        if text.startswith("```"):
            lines = text.splitlines()
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].startswith("```"):
                lines = lines[:-1]
            text = "\n".join(lines).strip()
        return json.loads(text)
    except Exception as e:
        logger.warning("Groq call failed or unavailable: %s", e)
        return None


class Synthesizer:
    """MAP-REDUCE LLM synthesizer for architecture candidates."""

    def synthesize(
        self,
        candidates: list[ArchCandidate],
        domain_files: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """
        Returns list of component dicts:
        {name, layer, kind, responsibility, technologies, confidence, files, evidence_ids}
        Falls back to converting ArchCandidates directly if LLM unavailable.
        """
        if not candidates:
            return []

        # MAP phase
        mapped: list[dict[str, Any]] = []
        for cand in candidates:
            prompt = _MAP_PROMPT.format(
                name=cand.name,
                kind=cand.kind,
                layer=cand.layer,
                files=cand.files[:10],
                technologies=cand.technologies,
            )
            result = _call_groq(prompt)
            if result and isinstance(result, dict) and "name" in result:
                result.setdefault("kind", cand.kind)
                result.setdefault("layer", cand.layer)
                result.setdefault("technologies", cand.technologies)
                result.setdefault("files", cand.files)
                result.setdefault("evidence_ids", cand.evidence_ids)
                mapped.append(result)
            else:
                # LLM-down fallback: use candidate as-is
                mapped.append({
                    "name": cand.name,
                    "layer": cand.layer,
                    "kind": cand.kind,
                    "responsibility": f"{cand.name} component ({cand.kind})",
                    "technologies": cand.technologies,
                    "files": cand.files,
                    "evidence_ids": cand.evidence_ids,
                    "confidence": round(cand.confidence * 0.85, 3),  # slightly lower without LLM verification
                })

        # REDUCE phase (only if we got >=2 mapped components and LLM is configured)
        if len(mapped) >= 2 and os.environ.get("GROQ_API_KEY"):
            reduce_prompt = _REDUCE_PROMPT.format(
                components_json=json.dumps(mapped, indent=2)
            )
            reduced = _call_groq(reduce_prompt)
            if reduced and isinstance(reduced, dict) and "components" in reduced and isinstance(reduced["components"], list):
                # Ensure files and evidence_ids are preserved
                cand_map = {c["name"].lower(): c for c in mapped}
                for rcomp in reduced["components"]:
                    orig = cand_map.get(rcomp.get("name", "").lower())
                    if orig:
                        rcomp.setdefault("files", orig.get("files", []))
                        rcomp.setdefault("evidence_ids", orig.get("evidence_ids", []))
                        if not rcomp.get("technologies"):
                            rcomp["technologies"] = orig.get("technologies", [])
                return reduced["components"]

        return mapped
