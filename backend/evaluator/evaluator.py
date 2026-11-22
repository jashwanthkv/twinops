"""
evaluator/evaluator.py

WHAT IT IS DOING:
  Acts as a lightweight verification gate between the Isolation Forest and the
  Master LLM. Calls llama-3.1-8b-instant on Groq with the anomaly context and
  parses a REAL or FALSE_POSITIVE verdict. This prevents the expensive Master
  LLM from being called on statistical noise.

HOW IT IS CODED:
  EvaluatorGate uses the Groq client (same API key pool as AIE KnowledgeExtractor).
  Prompt asks for VERDICT: REAL or VERDICT: FALSE_POSITIVE with one-sentence REASON.
  Parsing extracts the first occurrence of REAL or FALSE_POSITIVE from the response.
  On timeout or parse failure → defaults to REAL (fail-safe, never drop real problems).

DIAGNOSTIC GUIDE:
  If evaluator_audits in MongoDB shows many FALSE_POSITIVE verdicts on obvious
  problems, check if the ANOMALY_THRESHOLD is too loose. If all alerts are REAL,
  the threshold may be too strict (too many alerts reaching the evaluator).
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

EVALUATOR_MODEL = "llama-3.1-8b-instant"
_PROMPT_TEMPLATE = """\
You are the TwinOps Anomaly Evaluator.
An Isolation Forest detector flagged an alert.

Alert Details:
  Top anomalous metric: {top_metric}
  Anomaly score: {score} (higher = more anomalous)
  Current snapshot:
{snapshot}
  10-minute trend for {top_metric}: {trend}

Determine whether this is a GENUINE performance anomaly or a TEMPORARY BENIGN FLUCTUATION.

Output strictly in this format (two lines only):
VERDICT: REAL
REASON: <one sentence>

or

VERDICT: FALSE_POSITIVE
REASON: <one sentence>
"""


class EvaluatorGate:
    """Calls llama-3.1-8b-instant to verify an anomaly before escalating to the Master LLM."""

    def __init__(self) -> None:
        from groq import Groq
        # Use first available Groq key
        keys = [k for k in [
            os.getenv("GROQ_API_KEY", ""),
            os.getenv("GROQ_API_KEY1", ""),
            os.getenv("GROQ_API_KEY2", ""),
        ] if k]
        self._client = Groq(api_key=keys[0] if keys else "")

    def evaluate(
        self,
        top_metric: str,
        score: float,
        metric_snapshot: dict,
        trend_values: list[float],
    ) -> tuple[str, str]:
        """
        Returns (verdict, reason) where verdict is 'REAL' or 'FALSE_POSITIVE'.
        Defaults to 'REAL' on failure (fail-safe).
        """
        snapshot_lines = "\n".join(f"    {k}: {v}" for k, v in metric_snapshot.items())
        prompt = _PROMPT_TEMPLATE.format(
            top_metric=top_metric,
            score=score,
            snapshot=snapshot_lines,
            trend=trend_values,
        )
        try:
            response = self._client.chat.completions.create(
                model=EVALUATOR_MODEL,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                max_tokens=100,
                timeout=10,
            )
            text = response.choices[0].message.content or ""
            return self._parse(text)
        except Exception as exc:
            logger.warning("Evaluator call failed (%s) — defaulting to REAL (fail-safe)", exc)
            return "REAL", "Evaluator unavailable — defaulting to REAL for safety"

    def _parse(self, text: str) -> tuple[str, str]:
        verdict = "REAL"
        reason = ""
        for line in text.strip().splitlines():
            line = line.strip()
            if line.startswith("VERDICT:"):
                raw = line.split(":", 1)[1].strip().upper()
                if "FALSE" in raw or "FALSE_POSITIVE" in raw:
                    verdict = "FALSE_POSITIVE"
                else:
                    verdict = "REAL"
            elif line.startswith("REASON:"):
                reason = line.split(":", 1)[1].strip()
        return verdict, reason
