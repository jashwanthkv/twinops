"""Catalog resolver: maps Evidence items to known technology roles.

Loads YAML catalogs from aie/evidence/catalog/*.yaml and
matches each Evidence item against package names, image names, ports.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    import yaml
    _YAML_OK = True
except ImportError:
    _YAML_OK = False

from aie.evidence.models import Evidence, EvidenceBundle


@dataclass
class TechMatch:
    """A technology node resolved from catalog + evidence."""
    tech_id: str
    display_name: str
    layer: str
    possible_roles: list[str]
    evidence_ids: list[str] = field(default_factory=list)
    confidence: float = 0.0


class CatalogResolver:
    """Loads catalog YAML files and resolves Evidence → TechMatch."""

    _CATALOG_DIR = Path(__file__).parent / "catalog"

    def __init__(self) -> None:
        self._catalog: list[dict[str, Any]] = []
        self._loaded = False

    def _load(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        if not _YAML_OK:
            return
        for yaml_file in sorted(self._CATALOG_DIR.glob("*.yaml")):
            try:
                data = yaml.safe_load(yaml_file.read_text(encoding="utf-8"))
                if data and "technologies" in data:
                    self._catalog.extend(data["technologies"])
            except Exception:
                pass

    def resolve(self, bundle: EvidenceBundle) -> list[TechMatch]:
        self._load()
        matches: list[TechMatch] = []

        for entry in self._catalog:
            match_cfg = entry.get("match", {})
            matched_evidence: list[str] = []

            packages = {p.lower().replace("_", "-") for p in match_cfg.get("packages", [])}
            images = {img.split("/")[-1].split(":")[0].lower() for img in match_cfg.get("images", [])}
            ports = {str(p) for p in match_cfg.get("ports", [])}
            env_keys = {k.lower() for k in match_cfg.get("env_keys", [])}

            for ev in bundle.items:
                val = ev.value.lower().replace("_", "-")
                if ev.kind == "dependency" and val in packages:
                    matched_evidence.append(ev.id)
                elif ev.kind == "infra_service" and (val.split("/")[-1] in images or val in images):
                    matched_evidence.append(ev.id)
                elif ev.kind == "port" and val in ports:
                    matched_evidence.append(ev.id)
                elif ev.kind in ("env_var", "config_key") and val in env_keys:
                    matched_evidence.append(ev.id)

            if matched_evidence:
                # Confidence: more evidence items → higher confidence, capped at 0.95
                conf = min(0.95, 0.5 + 0.15 * len(matched_evidence))
                matches.append(TechMatch(
                    tech_id=entry["id"],
                    display_name=entry["display_name"],
                    layer=entry["layer"],
                    possible_roles=entry.get("possible_roles", []),
                    evidence_ids=matched_evidence,
                    confidence=conf,
                ))

        return matches
