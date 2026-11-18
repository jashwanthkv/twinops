"""Validator for LLM responses against COMPONENT_ANALYSIS_SCHEMA."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

import jsonschema

from aie.knowledge.schema import COMPONENT_ANALYSIS_SCHEMA

logger = logging.getLogger(__name__)

# Secret patterns to strip from LLM responses
_SECRET_PATTERNS = [
    re.compile(r"(?i)(password|secret|key|token|auth|bearer)\s*[:=]\s*['\"]?([^'\"\s\r\n]{6,})['\"]?"),
    re.compile(r"ghp_[A-Za-z0-9_]{36}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
]

_MAX_STRING_LENGTH = 10000


class LLMResponseValidator:
    """Validates, sanitizes, and truncates Groq LLM response JSON."""

    def __init__(self, schema: dict | None = None) -> None:
        self.schema = schema or COMPONENT_ANALYSIS_SCHEMA

    def validate(
        self,
        raw_response: str | dict[str, Any],
        component_id: str,
    ) -> dict[str, Any] | None:
        """Parse, validate against JSON Schema, sanitize secrets, and truncate fields.

        Returns the validated dictionary, or None if invalid or unparseable.
        Does not raise exceptions on validation or parsing failure.
        """
        # 1. Parse JSON
        parsed: dict[str, Any]
        if isinstance(raw_response, dict):
            parsed = dict(raw_response)
        elif isinstance(raw_response, str):
            clean_str = self._extract_json_text(raw_response)
            try:
                parsed = json.loads(clean_str)
                if not isinstance(parsed, dict):
                    logger.warning("LLM response is valid JSON but not an object: %s", type(parsed))
                    return None
            except Exception as exc:
                logger.warning(
                    "Failed to parse LLM response as JSON for component %s: %s",
                    component_id,
                    exc,
                )
                return None
        else:
            logger.warning("Unexpected raw_response type: %s", type(raw_response))
            return None

        # Ensure component_id is set
        if "component_id" not in parsed or not parsed["component_id"]:
            parsed["component_id"] = component_id

        # 2. Validate against schema
        try:
            jsonschema.validate(instance=parsed, schema=self.schema)
        except jsonschema.ValidationError as err:
            logger.warning(
                "LLM response failed JSON Schema validation for component %s: %s (path: %s)",
                component_id,
                err.message,
                list(err.path),
            )
            return None
        except Exception as exc:
            logger.warning("Unexpected error during schema validation: %s", exc)
            return None

        # 3. Strip secrets and truncate strings recursively
        sanitized = self._sanitize_and_truncate(parsed)
        return sanitized

    def _extract_json_text(self, text: str) -> str:
        """Extract JSON substring, stripping markdown fences if present."""
        text = text.strip()

        # Handle ```json ... ``` blocks
        fence_match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text)
        if fence_match:
            return fence_match.group(1).strip()

        # Handle curly brace substring { ... }
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            return text[start : end + 1]

        return text

    def _sanitize_and_truncate(self, data: Any) -> Any:
        """Recursively redact secret-like patterns and enforce string length caps."""
        if isinstance(data, str):
            cleaned = data
            for pattern in _SECRET_PATTERNS:
                cleaned = pattern.sub(
                    lambda m: f"{m.group(1)}: [REDACTED]" if m.lastindex and m.lastindex >= 2 else "[REDACTED]",
                    cleaned,
                )
            if len(cleaned) > _MAX_STRING_LENGTH:
                cleaned = cleaned[:_MAX_STRING_LENGTH] + "...[TRUNCATED]"
            return cleaned

        if isinstance(data, dict):
            new_dict: dict[str, Any] = {}
            for k, v in data.items():
                if any(sec in k.lower() for sec in ["secret", "password", "token", "private_key"]):
                    new_dict[k] = "[REDACTED]"
                else:
                    new_dict[k] = self._sanitize_and_truncate(v)
            return new_dict

        if isinstance(data, list):
            return [self._sanitize_and_truncate(item) for item in data]

        return data
