"""Discrepancy detector — discovers conflicts between config files, code settings, and documentation."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Literal

logger = logging.getLogger(__name__)


@dataclass
class Discrepancy:
    """Represents an architectural or operational inconsistency discovered across sources."""

    type: str
    description: str
    doc_value: str | None = None
    code_value: str | None = None
    severity: Literal["low", "medium", "high"] = "low"
    source_file: str = ""


class DiscrepancyDetector:
    """Detects discrepancies between configuration files, code implementations, and documentation claims."""

    def detect(
        self,
        config_findings: list[Any],
        components: list[Any],
        docs_knowledge: Any = None,
    ) -> list[Discrepancy]:
        """Detect mismatches between configuration, code, and documentation.

        Checks:
        1. Config vs Code: e.g. config file says POOL_SIZE=5 but code uses different pool sizing (max_overflow=10).
        2. Documentation vs Code: e.g. README mentions service X but no component was found.
        3. Config discrepancies across component metadata.
        """
        discrepancies: list[Discrepancy] = []

        # Index config findings by uppercase key
        config_by_key: dict[str, list[Any]] = {}
        for finding in config_findings or []:
            key = getattr(finding, "key", "")
            if key:
                config_by_key.setdefault(key.upper(), []).append(finding)

        # 1. Check: config file says POOL_SIZE=5 but code has max_overflow=10 / conflicting pool size
        pool_findings = config_by_key.get("POOL_SIZE", []) or config_by_key.get("DB_POOL_SIZE", [])
        for pf in pool_findings:
            pf_val = str(getattr(pf, "value", ""))
            pf_src = getattr(pf, "source_file", "config")

            # Check components for SQLAlchemy or database connection pool settings
            for comp in components:
                meta = getattr(comp, "metadata", {}) or {}
                code_pool_val = None
                code_overflow_val = None

                # Look in metadata or config findings
                if isinstance(meta, dict):
                    cfg = meta.get("configuration", {})
                    if isinstance(cfg, dict):
                        code_pool_val = cfg.get("pool_size") or cfg.get("POOL_SIZE")
                        code_overflow_val = cfg.get("max_overflow") or cfg.get("MAX_OVERFLOW")

                    # Also check code snippets or findings
                    for k, v in meta.items():
                        if "max_overflow" in k.lower():
                            code_overflow_val = v
                        if "pool_size" in k.lower():
                            code_pool_val = v

                # If code specifies max_overflow or differing pool_size
                if code_overflow_val and str(code_overflow_val) != pf_val:
                    discrepancies.append(
                        Discrepancy(
                            type="pool_configuration_conflict",
                            description=(
                                f"Config file '{pf_src}' defines POOL_SIZE={pf_val}, but component "
                                f"'{getattr(comp, 'name', '')}' specifies max_overflow={code_overflow_val}"
                            ),
                            doc_value=f"POOL_SIZE={pf_val}",
                            code_value=f"max_overflow={code_overflow_val}",
                            severity="medium",
                            source_file=pf_src,
                        )
                    )
                elif code_pool_val and str(code_pool_val) != pf_val and pf_val not in ("[REDACTED]", ""):
                    discrepancies.append(
                        Discrepancy(
                            type="pool_size_mismatch",
                            description=(
                                f"Config file '{pf_src}' defines POOL_SIZE={pf_val}, but component "
                                f"'{getattr(comp, 'name', '')}' code uses POOL_SIZE={code_pool_val}"
                            ),
                            doc_value=pf_val,
                            code_value=str(code_pool_val),
                            severity="medium",
                            source_file=pf_src,
                        )
                    )

        # 2. Check general component configurations vs file config findings
        for comp in components:
            meta = getattr(comp, "metadata", {}) or {}
            comp_cfg = {}
            if hasattr(comp, "configuration") and isinstance(comp.configuration, dict):
                comp_cfg = comp.configuration
            elif isinstance(meta, dict) and "configuration" in meta and isinstance(meta["configuration"], dict):
                comp_cfg = meta["configuration"]

            for cfg_key, code_val in comp_cfg.items():
                findings = config_by_key.get(str(cfg_key).upper(), [])
                for ff in findings:
                    ff_val = str(getattr(ff, "value", ""))
                    if ff_val != str(code_val) and ff_val not in ("[REDACTED]", ""):
                        discrepancies.append(
                            Discrepancy(
                                type="config_mismatch",
                                description=(
                                    f"Component '{getattr(comp, 'name', '')}' config '{cfg_key}' differs: "
                                    f"code has {code_val!r} but config file has {ff_val!r}"
                                ),
                                doc_value=ff_val,
                                code_value=str(code_val),
                                severity="medium",
                                source_file=getattr(ff, "source_file", ""),
                            )
                        )

        # 3. Check: README / doc mentions service X but no component was found
        comp_names_lower = {
            getattr(c, "name", "").lower().replace("-", "_").replace(" ", "_")
            for c in components
        }

        documented_services: list[str] = []
        if docs_knowledge:
            if isinstance(docs_knowledge, dict):
                arch = docs_knowledge.get("architecture_mentions", [])
                descs = docs_knowledge.get("component_descriptions", {})
                if isinstance(arch, list):
                    for a in arch:
                        if isinstance(a, str):
                            documented_services.append(a)
                if isinstance(descs, dict):
                    documented_services.extend(descs.keys())
            else:
                arch = getattr(docs_knowledge, "architecture_mentions", [])
                descs = getattr(docs_knowledge, "component_descriptions", {})
                if isinstance(arch, list):
                    for a in arch:
                        if isinstance(a, str):
                            documented_services.append(a)
                if isinstance(descs, dict):
                    documented_services.extend(descs.keys())

        for doc_item in documented_services:
            clean_item = doc_item.lower().replace("-", "_").replace(" ", "_")
            # Filter to candidates that look like service / component names
            if any(term in clean_item for term in ["service", "server", "worker", "gateway", "database", "api"]):
                # Check if this service is covered by any discovered component
                if not any(clean_item in cname or cname in clean_item for cname in comp_names_lower):
                    discrepancies.append(
                        Discrepancy(
                            type="undiscovered_service_mention",
                            description=(
                                f"Documentation mentions '{doc_item}', but no matching component "
                                "was discovered in the codebase"
                            ),
                            doc_value=doc_item,
                            code_value=None,
                            severity="high",
                            source_file="README.md / docs",
                        )
                    )

        logger.info("Discrepancy detection found %d discrepancies.", len(discrepancies))
        return discrepancies
