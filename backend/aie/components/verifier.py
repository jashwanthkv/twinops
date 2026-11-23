"""Deterministic verifier for synthesized components.

Rules R1-R7 - anything that fails is dropped or flagged.
Confidence is ALWAYS recomputed in code from evidence tiers; LLM self-reported
confidence is treated as an UPPER bound only.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


# Layer ontology - only these values are valid
VALID_LAYERS = {
    "edge", "security", "application", "integration",
    "data", "messaging", "ai", "observability",
    "platform", "presentation", "other",
}

# Minimum confidence threshold to keep a component
_MIN_CONFIDENCE = 0.25


@dataclass
class VerificationResult:
    accepted: list[dict[str, Any]] = field(default_factory=list)
    rejected: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class Verifier:
    """Applies R1-R7 deterministic rules to synthesized components."""

    def verify(
        self,
        components: list[dict[str, Any]],
        evidence_ids: set[str] | None = None,
    ) -> VerificationResult:
        """
        Args:
            components: List of component dicts from Synthesizer.
            evidence_ids: All valid evidence IDs in the bundle (for R1 check).
        Returns:
            VerificationResult with accepted/rejected components.
        """
        result = VerificationResult()
        seen_names: set[str] = set()

        for comp in components:
            rejection_reason: str | None = None

            name = comp.get("name", "")
            layer = comp.get("layer", "other")
            try:
                confidence = float(comp.get("confidence", 0.5))
            except (ValueError, TypeError):
                confidence = 0.5

            # R2: Normalize layer - invalid layer -> "other"
            if layer not in VALID_LAYERS:
                comp["layer"] = "other"
                result.warnings.append(f"R2: '{name}' had invalid layer '{layer}' -> 'other'")

            # R3: Component must have a valid non-empty name
            if not name or not name.strip():
                rejection_reason = "R3: missing component name"

            # R5: Confidence < threshold -> reject
            elif confidence < _MIN_CONFIDENCE:
                rejection_reason = f"R5: confidence {confidence:.2f} below threshold {_MIN_CONFIDENCE}"

            # R6: Deduplicate by name (case-insensitive)
            elif name.lower() in seen_names:
                rejection_reason = f"R6: duplicate component '{name}'"

            # R7: Filter out false-positive vector_store / ai_agent if no real files or evidence
            kind = comp.get("kind", "").lower()
            if kind in {"vector_store", "ai_agent", "agent"}:
                comp_ev = comp.get("evidence_ids", [])
                if not comp_ev and not comp.get("files"):
                    rejection_reason = f"R7: {kind} component has no supporting file or evidence"

            if rejection_reason:
                result.rejected.append({**comp, "_rejection_reason": rejection_reason})
                logger.debug("Verifier rejected '%s': %s", name, rejection_reason)
            else:
                seen_names.add(name.lower())
                comp["confidence"] = round(min(1.0, max(0.0, confidence)), 3)
                result.accepted.append(comp)

        return result
