"""
llm/prompts.py

WHAT IT IS DOING:
  Structured prompt templates for the Master LLM. Enforces deep root-cause
  analysis based on the injected AIE architecture knowledge, source excerpts,
  operational knobs, and live Digital Twin metrics.

HOW IT IS CODED:
  MASTER_ANALYSIS_SYSTEM_PROMPT sets role as TwinOps Principal Reliability Engineer.
  build_master_prompt() formats the context dict into a clear markdown prompt
  and requests strict JSON output.
"""
from __future__ import annotations

import json
from typing import Any

MASTER_ANALYSIS_SYSTEM_PROMPT = """\
You are TwinOps Master Intelligence, an autonomous site reliability and root-cause analysis engine.
You have access to the deep static architecture model of the application (discovered components,
3-part code descriptions, call graph relationships, operational knobs) alongside live telemetry
and digital twin health states.

Your goal is to pinpoint the exact root-cause component, explain how the software architecture
and runtime configuration created this condition, determine the failure impact radius, and
provide concrete, ordered remediation actions (code patches and config knob changes).

You must respond ONLY with a valid, parseable JSON object matching the required schema.
Do not include markdown code block formatting (```json) or extra conversational text.
"""

_USER_PROMPT_TEMPLATE = """\
### INCIDENT CONTEXT
- Application ID: {app_id}
- Trigger Mechanism: {trigger_type}
- Trigger Details:
{trigger_json}

### APPLICATION TECHNOLOGY PROFILE
- Primary Language: {primary_lang}
- Frameworks: {frameworks}
- Language Frameworks Map: {lang_frameworks}

### SYSTEM DESIGN COMPONENTS & ARCHITECTURE
{components_block}

### CALL GRAPH & COMPONENT RELATIONSHIPS
{relationships_block}

### OPERATIONAL KNOBS & CONFIGURATION (AST EXTRACTED)
{knobs_block}

### LIVE DIGITAL TWIN TELEMETRY
- Current App Health: {twin_health}
- Active Anomalies: {twin_anomalies}
- Recent Metric Snapshot:
{metrics_snapshot}

---
### REQUIRED OUTPUT JSON SCHEMA
Produce a JSON response strictly conforming to this structure:
{{
  "incident_id": "inc-{app_id}-<unique_id>",
  "app_id": "{app_id}",
  "root_cause_component_id": "<ID of the component initiating the fault>",
  "root_cause_summary": "<One-sentence root-cause statement>",
  "impact_radius": [
    "<component_id_1>",
    "<component_id_2>"
  ],
  "technical_explanation": "<Detailed architectural breakdown explaining how the code, queries, or configuration caused this symptom. Reference specific class names, files, or operational knobs.>",
  "remediation_actions": [
    {{
      "step": 1,
      "type": "CONFIG_CHANGE | CODE_PATCH | RESTART | SCALE_UP",
      "target_file": "<relative file path if known>",
      "knob": "<knob name if CONFIG_CHANGE>",
      "current_value": "<current value if known>",
      "recommended_value": "<recommended change>",
      "description": "<Actionable instruction for the engineer>"
    }}
  ],
  "forecast_trajectory": "<Forecast assessment: what will happen in the next 15-60 minutes if no remediation is taken.>"
}}
"""


def build_master_prompt(context: dict[str, Any]) -> str:
    """Format full assembled context into the prompt string for Master LLM."""
    app_id = context.get("app_id", "unknown")
    trigger_type = context.get("trigger_type", "ANOMALY")
    trigger_json = json.dumps(context.get("trigger_data", {}), indent=2)

    tech = context.get("tech_profile", {})
    primary_lang = tech.get("primary_language", "Unknown")
    frameworks = ", ".join(tech.get("frameworks", [])) or "None detected"
    lang_frameworks = json.dumps(tech.get("language_primary_frameworks", {}))

    # Format components with full 3-part descriptions
    comp_lines: list[str] = []
    for c in context.get("components", []):
        cid = c.get("component_id")
        name = c.get("name")
        ctype = c.get("component_type")
        paths = ", ".join(c.get("root_paths", [])) or "root"
        techs = ", ".join(c.get("technologies", [])) or "None"
        desc = c.get("description", "No description available.")
        comp_lines.append(
            f"Component [{cid}] - {name} ({ctype})\n"
            f"  Technologies: {techs}\n"
            f"  Paths: {paths}\n"
            f"  Description:\n{desc}\n"
        )
    components_block = "\n".join(comp_lines) if comp_lines else "No component details stored."

    # Format relationships
    rel_lines: list[str] = []
    for r in context.get("relationships", []):
        rel_lines.append(f"  • {r.get('source')} --[{r.get('relation_type')}]--> {r.get('target')} : {r.get('description')}")
    relationships_block = "\n".join(rel_lines) if rel_lines else "No call graph edges stored."

    # Format operational knobs
    knobs_lines: list[str] = []
    for k in context.get("operational_knobs", []):
        knobs_lines.append(f"  • {k.get('name')} = {k.get('value')} ({k.get('file', '')}:{k.get('line', '')}) - {k.get('symptom', '')}")
    knobs_block = "\n".join(knobs_lines) if knobs_lines else "No operational knobs detected."

    # Digital twin
    twin = context.get("digital_twin", {})
    twin_health = twin.get("health", "UNKNOWN")
    twin_anomalies = json.dumps(twin.get("active_anomalies", []))
    metrics_snapshot = json.dumps(twin.get("latest_metrics", {}), indent=2)

    return _USER_PROMPT_TEMPLATE.format(
        app_id=app_id,
        trigger_type=trigger_type,
        trigger_json=trigger_json,
        primary_lang=primary_lang,
        frameworks=frameworks,
        lang_frameworks=lang_frameworks,
        components_block=components_block,
        relationships_block=relationships_block,
        knobs_block=knobs_block,
        twin_health=twin_health,
        twin_anomalies=twin_anomalies,
        metrics_snapshot=metrics_snapshot,
    )
