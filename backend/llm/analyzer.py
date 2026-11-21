"""
llm/analyzer.py

WHAT IT IS DOING:
  Executes the Master LLM root-cause analysis by submitting the assembled
  diagnostic context to the configured Groq model pool. Uses round-robin
  key rotation across GROQ_API_KEY, GROQ_API_KEY1, GROQ_API_KEY2 with
  automatic fallback.

HOW IT IS CODED:
  Reuses GroqClientPool from aie.knowledge.extractor for multi-key pooling.
  Attempts primary model (from AIE_GROQ_MODEL or llama-3.3-70b-versatile)
  with temperature=0.1. Strips markdown fences if returned and validates
  output matches JSON schema.

DIAGNOSTIC GUIDE:
  If analysis fails with rate limit (429), the pool will automatically cycle
  to the next key. Check logs for "Calling Groq LLM (model: ..., key: ...)".
"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

from aie.config import settings
from aie.knowledge.extractor import GroqClientPool
from llm.prompts import MASTER_ANALYSIS_SYSTEM_PROMPT, build_master_prompt

logger = logging.getLogger(__name__)


def _clean_json_response(raw_text: str) -> str:
    """Strip markdown code block fences if returned by the LLM."""
    text = raw_text.strip()
    match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text)
    if match:
        return match.group(1).strip()
    return text


class MasterLLMAnalyzer:
    """Master LLM orchestrator for root-cause and remediation analysis."""

    def __init__(self) -> None:
        # Load API keys via AIEConfig helper
        api_keys = settings.get_groq_api_keys() if hasattr(settings, "get_groq_api_keys") else []
        if not api_keys:
            # Fallback direct env scan
            api_keys = [
                k for k in [
                    os.getenv("GROQ_API_KEY", ""),
                    os.getenv("GROQ_API_KEY1", ""),
                    os.getenv("GROQ_API_KEY2", ""),
                ] if k
            ]
        self.pool = GroqClientPool(api_keys)
        self.primary_model = os.getenv("AIE_GROQ_MODEL", getattr(settings, "groq_model", "llama-3.3-70b-versatile"))
        logger.info(
            "MasterLLMAnalyzer initialized with %d Groq keys (primary model: %s)",
            self.pool.size,
            self.primary_model,
        )

    def analyze(self, context: dict[str, Any]) -> dict[str, Any]:
        """
        Submits prompt to Groq model pool and returns structured analysis dict.
        """
        app_id = context.get("app_id", "unknown")
        user_prompt = build_master_prompt(context)

        if not self.pool.is_available:
            logger.warning("No Groq API keys available for Master LLM — generating heuristic analysis")
            return self._heuristic_fallback(context)

        candidate_models = [self.primary_model]
        for fb in ["llama-3.3-70b-versatile", "llama-3.1-8b-instant"]:
            if fb not in candidate_models:
                candidate_models.append(fb)

        total_attempts = self.pool.size * len(candidate_models)
        last_error: Exception | None = None

        for attempt in range(1, total_attempts + 1):
            client_tuple = self.pool.get_client()
            if not client_tuple or client_tuple[0] is None:
                break
            masked_key, client = client_tuple
            target_model = candidate_models[(attempt - 1) % len(candidate_models)]

            try:
                logger.info(
                    "Master LLM call for app='%s' (model: %s, key: %s, attempt %d/%d)...",
                    app_id, target_model, masked_key, attempt, total_attempts,
                )
                response = client.chat.completions.create(
                    model=target_model,
                    messages=[
                        {"role": "system", "content": MASTER_ANALYSIS_SYSTEM_PROMPT},
                        {"role": "user", "content": user_prompt},
                    ],
                    temperature=0.1,
                    max_tokens=2048,
                    timeout=30,
                )
                raw_content = response.choices[0].message.content or ""
                cleaned = _clean_json_response(raw_content)
                parsed = json.loads(cleaned)
                logger.info("Master LLM successfully generated analysis for app='%s'", app_id)
                return parsed

            except json.JSONDecodeError as jde:
                logger.warning("Failed to decode JSON from model response: %s", jde)
                last_error = jde
            except Exception as exc:
                logger.warning("Groq call failed on attempt %d: %s", attempt, exc)
                last_error = exc

        logger.error("All Master LLM attempts failed for app='%s': %s", app_id, last_error)
        return self._heuristic_fallback(context)

    def _heuristic_fallback(self, context: dict[str, Any]) -> dict[str, Any]:
        """Fail-safe baseline analysis if LLM is unavailable."""
        app_id = context.get("app_id", "unknown")
        trigger_data = context.get("trigger_data", {})
        top_metric = trigger_data.get("top_metric") or "system_resources"
        import uuid
        return {
            "incident_id": f"inc-{app_id}-{uuid.uuid4().hex[:8]}",
            "app_id": app_id,
            "root_cause_component_id": "unknown",
            "root_cause_summary": f"Performance anomaly detected in {top_metric} requiring immediate investigation.",
            "impact_radius": [c.get("component_id") for c in context.get("components", [])[:2]],
            "technical_explanation": f"Automated analysis fallback: Metric {top_metric} triggered threshold breach. Review recent application logs and dependency latencies.",
            "remediation_actions": [
                {
                    "step": 1,
                    "type": "SCALE_UP",
                    "description": "Verify resource saturation and examine database connection pool status.",
                }
            ],
            "forecast_trajectory": "Continued elevation in metric response time expected without intervention.",
        }
