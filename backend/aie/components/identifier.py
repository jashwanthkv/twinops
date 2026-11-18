"""Component identification coordinator running heuristic and static rules."""

from __future__ import annotations

import logging
from pathlib import PurePosixPath
import uuid
from typing import Any

from aie.components.rules.cache import CacheRule
from aie.components.rules.data_access import DataAccessRule
from aie.components.rules.database import DatabaseRule
from aie.components.rules.web_tier import WebTierRule
from aie.components.scoring import ComponentCandidate, ComponentScorer
from aie.models import ComponentType, DiscoveredComponent, TechnologyProfile
from aie.static_analysis.ir import CodebaseIR

logger = logging.getLogger(__name__)


def _extract_root_paths(files: list[str]) -> list[str]:
    """Derive unique directory root paths from a list of file paths."""
    dirs: set[str] = set()
    for f in files:
        # Normalize to POSIX
        posix = f.replace("\\", "/")
        parent = str(PurePosixPath(posix).parent)
        if parent and parent != ".":
            dirs.add(parent)
    return sorted(list(dirs))


class ComponentIdentifier:
    """Discovers high-level architectural components by running specialized rule engines

    over CodebaseIR, profile, and documentation knowledge.
    """

    MIN_SCORE_THRESHOLD = 0.3

    def __init__(self) -> None:
        self.scorer = ComponentScorer()
        self.rules = [
            WebTierRule(),
            DataAccessRule(),
            CacheRule(),
            DatabaseRule(),
        ]

    def identify(
        self,
        codebase_ir: CodebaseIR,
        profile: TechnologyProfile | None = None,
        docs_knowledge: Any = None,
        inventory: Any = None,
    ) -> list[DiscoveredComponent]:
        """Execute all rules, score candidates, deduplicate, and return DiscoveredComponents."""
        raw_candidates: list[ComponentCandidate] = []

        # 1. Run each rule
        for rule in self.rules:
            try:
                candidates = rule.detect(codebase_ir, profile, docs_knowledge)
                raw_candidates.extend(candidates)
            except Exception as exc:
                logger.warning("Error running rule %s: %s", type(rule).__name__, exc)

        # 2. Score and filter candidates
        scored_candidates: list[tuple[ComponentCandidate, float]] = []
        for cand in raw_candidates:
            final_score = self.scorer.score(cand.score_evidence)
            if final_score >= self.MIN_SCORE_THRESHOLD:
                scored_candidates.append((cand, final_score))
            else:
                logger.debug(
                    "Dropping candidate '%s' (score %.2f < %.2f)",
                    cand.name,
                    final_score,
                    self.MIN_SCORE_THRESHOLD,
                )

        # 3. Deduplicate candidates by (type, technology) or name
        deduped: dict[tuple[str, str], tuple[ComponentCandidate, float]] = {}
        for cand, score in scored_candidates:
            type_str = str(cand.type)
            tech_str = cand.technology.lower()
            key = (type_str, tech_str)

            if key not in deduped:
                deduped[key] = (cand, score)
            else:
                existing_cand, existing_score = deduped[key]
                # Merge evidence and files
                merged_files = sorted(list(set(existing_cand.files + cand.files)))
                merged_evidence = existing_cand.evidence + [
                    e for e in cand.evidence if e not in existing_cand.evidence
                ]
                best_score = max(existing_score, score)
                existing_cand.files = merged_files
                existing_cand.evidence = merged_evidence[:20]
                deduped[key] = (existing_cand, best_score)

        # 4. Construct DiscoveredComponent instances
        run_id = getattr(profile, "run_id", "") or str(uuid.uuid4())
        primary_lang = None
        if profile and hasattr(profile, "languages") and profile.languages:
            primary_lang = profile.languages[0].name

        components: list[DiscoveredComponent] = []

        for (type_str, _), (cand, confidence) in deduped.items():
            comp_type = ComponentType.UNKNOWN
            try:
                comp_type = ComponentType(cand.type)
            except (ValueError, TypeError):
                # Fallback to matching string
                for member in ComponentType:
                    if member.value == type_str.lower():
                        comp_type = member
                        break

            root_dirs = _extract_root_paths(cand.files)
            technologies = [cand.technology] if cand.technology else []

            comp = DiscoveredComponent(
                component_id=str(uuid.uuid4()),
                run_id=run_id,
                name=cand.name,
                component_type=comp_type,
                description=f"{cand.technology} component providing {comp_type.value} functionality.",
                primary_language=primary_lang,
                root_paths=root_dirs,
                technologies=technologies,
                exposed_ports=[],
                environment_variables=[],
                evidence=cand.evidence,
                confidence=confidence,
                metadata={
                    "score_breakdown": cand.score_evidence,
                    "file_count": len(cand.files),
                    **cand.metadata,
                },
            )
            components.append(comp)

        logger.info("Identified %d components across the codebase", len(components))
        return components
