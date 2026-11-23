"""
AIE v2 Eval Harness.

Loads golden repo configs and checks AIE output against expected/forbidden components.
Usage: python eval/run_eval.py [--golden eval/golden/fastapi_boilerplate.yaml]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

try:
    import yaml
    _YAML_OK = True
except ImportError:
    _YAML_OK = False
    print("WARNING: PyYAML not installed.", file=sys.stderr)


def check_golden(golden_path: str, components: list[dict]) -> dict:
    """Check AIE output components against a golden spec."""
    if not _YAML_OK:
        return {"error": "PyYAML not available"}

    spec = yaml.safe_load(Path(golden_path).read_text(encoding="utf-8"))
    results = {
        "repo": spec.get("repo", ""),
        "passed": [],
        "failed": [],
        "forbidden_found": [],
        "precision": 0.0,
        "recall": 0.0,
        "hallucination_rate": 0.0,
    }

    # Check forbidden components
    forbidden_names = spec.get("forbidden_component_names_containing", [])
    forbidden_kinds = spec.get("forbidden_component_kinds", [])

    for comp in components:
        name = comp.get("name", "")
        kind = comp.get("kind", comp.get("component_type", ""))
        for fb_name in forbidden_names:
            if fb_name.lower() in name.lower():
                results["forbidden_found"].append({"name": name, "reason": f"name contains '{fb_name}'"})
        for fb_kind in forbidden_kinds:
            if fb_kind == kind:
                results["forbidden_found"].append({"name": name, "reason": f"kind '{kind}' is forbidden"})

    # Check expected components
    expected = spec.get("expected_components", [])
    for exp in expected:
        exp_kind = exp["kind"]
        min_conf = exp.get("min_confidence", 0.0)
        found = any(
            (comp.get("kind", "") == exp_kind or comp.get("component_type", "") == exp_kind)
            and float(comp.get("confidence", 1.0)) >= min_conf
            for comp in components
        )
        if found:
            results["passed"].append(exp)
        else:
            results["failed"].append(exp)

    # Metrics
    total_expected = len(expected)
    if total_expected:
        results["recall"] = len(results["passed"]) / total_expected
    total_found = len(components)
    if total_found:
        results["precision"] = len(results["passed"]) / total_found
    if total_found:
        results["hallucination_rate"] = len(results["forbidden_found"]) / total_found

    return results


def print_report(results: dict) -> None:
    repo = results.get("repo", "unknown")
    print(f"\n{'='*60}")
    print(f"Repo: {repo}")
    total_req = len(results["passed"]) + len(results["failed"])
    print(f"Passed:  {len(results['passed'])} / {total_req} expected")
    print(f"Precision:         {results['precision']:.2%}")
    print(f"Recall:            {results['recall']:.2%}")
    print(f"Hallucination rate:{results['hallucination_rate']:.2%}")

    if results["forbidden_found"]:
        print(f"\n❌ FORBIDDEN components found ({len(results['forbidden_found'])}):")
        for fb in results["forbidden_found"]:
            print(f"   - {fb['name']}: {fb['reason']}")
    else:
        print("\n✅ Zero forbidden components found (Clean)")

    if results["failed"]:
        print(f"\n⚠️  Missing expected components:")
        for f in results["failed"]:
            print(f"   - {f['kind']}: {f.get('description', '')}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="AIE v2 Eval Harness")
    parser.add_argument("--golden", default="eval/golden/fastapi_boilerplate.yaml")
    parser.add_argument("--components-json", help="JSON file with components list")
    args = parser.parse_args()

    if args.components_json:
        components = json.loads(Path(args.components_json).read_text(encoding="utf-8"))
    else:
        # Test sample matching FastAPI-boilerplate discovery
        components = [
            {"name": "API Routing", "kind": "api_routing", "confidence": 0.8},
            {"name": "Authentication", "kind": "auth", "confidence": 0.8},
            {"name": "Rate Limiting", "kind": "rate_limiter", "confidence": 0.8},
            {"name": "Redis Cache", "kind": "cache", "confidence": 0.85},
            {"name": "PostgreSQL Database", "kind": "database", "confidence": 0.85},
            {"name": "Domain Business Logic", "kind": "domain_service", "confidence": 0.7},
        ]
        print("Using simulated FastAPI-boilerplate v2 discovery output:")

    results = check_golden(args.golden, components)
    print_report(results)

    sys.exit(1 if results.get("forbidden_found") else 0)
