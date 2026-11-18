"""JSON Schema for validating Groq LLM component analysis responses."""

from __future__ import annotations

COMPONENT_ANALYSIS_SCHEMA: dict = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "title": "ComponentAnalysisResponse",
    "type": "object",
    "required": [
        "component_id",
        "description",
        "responsibility",
        "implementation_summary",
        "behavior",
        "failure_scenarios",
        "monitoring_requirements",
    ],
    "properties": {
        "component_id": {
            "type": "string",
            "description": "Unique identifier of the analyzed component",
        },
        "description": {
            "type": "string",
            "description": "Clear and comprehensive narrative explaining what the component is doing, how it is coded, and how to understand and diagnose issues when problems occur.",
            "maxLength": 6000,
        },
        "responsibility": {
            "type": "string",
            "description": "Primary architectural role and responsibility of the component",
            "maxLength": 3000,
        },
        "implementation_summary": {
            "type": "string",
            "description": "Summary of how the component is constructed and implemented",
            "maxLength": 5000,
        },
        "behavior": {
            "description": "Runtime operational behavior, request handling, or data processing logic",
            "oneOf": [
                {"type": "string", "maxLength": 5000},
                {
                    "type": "object",
                    "properties": {
                        "inputs": {"type": "array", "items": {"type": "string"}},
                        "outputs": {"type": "array", "items": {"type": "string"}},
                        "side_effects": {"type": "array", "items": {"type": "string"}},
                        "summary": {"type": "string", "maxLength": 3000},
                    },
                },
            ],
        },
        "failure_scenarios": {
            "type": "array",
            "description": "Potential technical failure modes, root causes, and blast radiuses",
            "items": {
                "oneOf": [
                    {"type": "string", "maxLength": 1000},
                    {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string", "maxLength": 200},
                            "scenario": {"type": "string", "maxLength": 1000},
                            "impact": {"type": "string", "maxLength": 500},
                            "mitigation": {"type": "string", "maxLength": 500},
                            "probability": {"type": "string"},
                        },
                    },
                ]
            },
        },
        "monitoring_requirements": {
            "type": "array",
            "description": "Observability indicators, metrics, health checks, and logs needed",
            "items": {
                "oneOf": [
                    {"type": "string", "maxLength": 500},
                    {
                        "type": "object",
                        "properties": {
                            "metric": {"type": "string", "maxLength": 200},
                            "type": {"type": "string", "maxLength": 100},
                            "description": {"type": "string", "maxLength": 500},
                            "threshold": {"type": "string", "maxLength": 200},
                        },
                    },
                ]
            },
        },
        "adaptation_opportunities": {
            "type": "array",
            "description": "Dynamic self-adaptation or optimization opportunities",
            "items": {
                "oneOf": [
                    {"type": "string", "maxLength": 1000},
                    {
                        "type": "object",
                        "properties": {
                            "opportunity": {"type": "string", "maxLength": 300},
                            "action": {"type": "string", "maxLength": 500},
                            "reversible": {"type": "boolean"},
                            "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                            "risk": {"type": "string"},
                        },
                    },
                ]
            },
        },
        "configuration_findings": {
            "type": "array",
            "description": "Discovered configuration keys, defaults, and source locations",
            "items": {
                "type": "object",
                "properties": {
                    "key": {"type": "string"},
                    "value": {"type": ["string", "number", "boolean", "null"]},
                    "source": {"type": "string"},
                    "line": {"type": ["integer", "null"]},
                },
            },
        },
        "confidence": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "description": "Self-assessed confidence in the analysis completeness",
        },
    },
    "additionalProperties": True,
}
