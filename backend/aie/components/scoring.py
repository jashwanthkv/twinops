"""Scoring engine and candidate data model for component identification."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field
from aie.models import ComponentType, SourceEvidence


class ComponentCandidate(BaseModel):
    """A detected component hypothesis before final scoring and deduplication."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    name: str
    type: ComponentType | str
    technology: str
    evidence: list[SourceEvidence] = Field(default_factory=list)
    files: list[str] = Field(default_factory=list)
    score_evidence: dict[str, float] = Field(default_factory=dict)
    metadata: dict = Field(default_factory=dict)


class ComponentScorer:
    """Computes a composite confidence score for component candidates."""

    WEIGHT_IMPORTS = 0.25
    WEIGHT_CODE_BEHAVIOR = 0.30
    WEIGHT_CONFIG = 0.20
    WEIGHT_FRAMEWORK_CONVENTION = 0.15
    WEIGHT_DOCS = 0.10

    def score(self, candidate_evidence: dict[str, float]) -> float:
        """Calculate weighted score between 0.0 and 1.0 from evidence dimension values."""
        val_imports = float(candidate_evidence.get("imports", 0.0))
        val_code_behavior = float(candidate_evidence.get("code_behavior", 0.0))
        val_config = float(candidate_evidence.get("config", 0.0))
        val_framework = float(candidate_evidence.get("framework_convention", 0.0))
        val_docs = float(candidate_evidence.get("docs", 0.0))

        weighted = (
            val_imports * self.WEIGHT_IMPORTS
            + val_code_behavior * self.WEIGHT_CODE_BEHAVIOR
            + val_config * self.WEIGHT_CONFIG
            + val_framework * self.WEIGHT_FRAMEWORK_CONVENTION
            + val_docs * self.WEIGHT_DOCS
        )

        return round(min(1.0, max(0.0, weighted)), 4)
