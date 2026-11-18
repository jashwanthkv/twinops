"""Confidence calculator — computes overall confidence score for a component."""

from __future__ import annotations

import logging
from typing import Any

from aie.models import DiscoveredComponent, EvidenceType

logger = logging.getLogger(__name__)


class ConfidenceCalculator:
    """Calculates confidence score (0.0–1.0) for a discovered component based on multiple factors."""

    def calculate(self, component: DiscoveredComponent | Any) -> float:
        """Calculate overall confidence score (0.0 to 1.0) for a component.

        Factors:
        1. Number of evidence sources (quantity)
        2. Evidence type diversity (multiple types like FILE_PATTERN, FILE_CONTENT, etc.)
        3. LLM confidence (if component was evaluated by LLM)
        4. Source file count (files associated with component)
        """
        # 1. Retrieve evidence items (check both evidence and source_evidence)
        evidences = getattr(component, "evidence", None) or getattr(component, "source_evidence", None) or []

        if not evidences:
            return 0.2  # Minimal baseline if component exists without evidence

        # Extract confidence values and evidence types
        evidence_confidences: list[float] = []
        type_coverage: set[str] = set()
        source_files: set[str] = set()

        for ev in evidences:
            conf = getattr(ev, "confidence", 1.0)
            if isinstance(conf, (int, float)):
                evidence_confidences.append(float(conf))

            # Handle both evidence_type and extraction_method
            ev_type = getattr(ev, "evidence_type", None) or getattr(ev, "extraction_method", None)
            if ev_type:
                type_coverage.add(str(ev_type))

            src_path = getattr(ev, "source_path", None)
            if src_path:
                source_files.add(src_path)

        # Also count root_paths or metadata files for source file count
        root_paths = getattr(component, "root_paths", []) or []
        for rp in root_paths:
            source_files.add(rp)

        meta = getattr(component, "metadata", {}) or {}
        meta_files = meta.get("files", []) if isinstance(meta, dict) else []
        for mf in meta_files:
            source_files.add(str(mf))

        # Base confidence: average of evidence items
        if evidence_confidences:
            base_score = sum(evidence_confidences) / len(evidence_confidences)
        else:
            base_score = 0.5

        # 1. Evidence quantity factor (up to +0.10)
        quantity_bonus = min(0.10, len(evidences) * 0.02)

        # 2. Evidence diversity factor (up to +0.15)
        diversity_bonus = min(0.15, len(type_coverage) * 0.05)

        # 3. Source file count factor (up to +0.10)
        file_bonus = min(0.10, len(source_files) * 0.025)

        # 4. LLM confidence factor (up to +0.10)
        llm_confidence = 0.0
        if hasattr(component, "_llm_confidence") and component._llm_confidence:
            llm_confidence = float(component._llm_confidence)
        elif isinstance(meta, dict) and "llm_knowledge" in meta:
            llm_k = meta["llm_knowledge"]
            if isinstance(llm_k, dict) and "confidence" in llm_k:
                llm_confidence = float(llm_k["confidence"])
        elif isinstance(meta, dict) and "confidence" in meta:
            llm_confidence = float(meta["confidence"])

        llm_bonus = min(0.10, llm_confidence * 0.10)

        total_score = (base_score * 0.65) + quantity_bonus + diversity_bonus + file_bonus + llm_bonus
        final_score = max(0.0, min(1.0, total_score))
        return round(final_score, 3)
