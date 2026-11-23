"""Evidence data model for the AIE v2 architecture discovery engine."""
from __future__ import annotations
import hashlib
from dataclasses import dataclass, field


TIER_WEIGHTS = {"A": 1.0, "B": 0.6, "C": 0.2}


@dataclass(frozen=True)
class Evidence:
    """An atomic, traceable piece of evidence supporting an architecture claim.

    Tiers:
      A — Proof-of-use: can CREATE a component on its own.
          Sources: package imports in requirements/pyproject, docker-compose images/ports,
          infra config keys, ORM model definitions.
      B — Structural role: LOCATES or CONNECTS a component; supports but cannot create.
          Sources: class inheritance, decorator patterns, DI parameters, directory structure.
      C — Lexical hint: SUPPORTS only; never creates or locates alone.
          Sources: class names, function names, variable names, comments.
    """

    id: str           # stable hash(kind, file, line, symbol)
    kind: str         # dependency|import|instantiation|decorator|route|middleware_reg|
                      # di_param|config_key|infra_service|env_var|port|upstream|
                      # entity|dir_name|symbol_name
    tier: str         # "A" | "B" | "C"
    file: str         # repo-relative path
    line: int | None  # source line, if applicable
    symbol: str | None  # e.g. class name, function name, import alias
    value: str        # the matched value (package name, image name, key, etc.)
    ecosystem: str    # python|node|jvm|go|infra|unknown

    @staticmethod
    def make_id(kind: str, file: str, line: int | None, symbol: str | None) -> str:
        raw = f"{kind}:{file}:{line}:{symbol}"
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

    @property
    def weight(self) -> float:
        return TIER_WEIGHTS.get(self.tier, 0.1)


@dataclass
class EvidenceBundle:
    """A collection of Evidence items for one analysis run."""

    items: list[Evidence] = field(default_factory=list)

    def add(self, ev: Evidence) -> None:
        self.items.append(ev)

    def by_tier(self, tier: str) -> list[Evidence]:
        return [e for e in self.items if e.tier == tier]

    def by_kind(self, kind: str) -> list[Evidence]:
        return [e for e in self.items if e.kind == kind]

    def by_file(self, file: str) -> list[Evidence]:
        return [e for e in self.items if e.file == file]

    def has_tier_a(self) -> bool:
        return any(e.tier == "A" for e in self.items)

    def noisy_or_confidence(self) -> float:
        """Deterministic confidence: noisy-OR of tier weights."""
        result = 0.0
        for ev in self.items:
            result = 1.0 - (1.0 - result) * (1.0 - ev.weight)
        return round(result, 3)
