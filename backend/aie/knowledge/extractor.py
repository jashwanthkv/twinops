"""
aie/knowledge/extractor.py — Component knowledge extractor using Groq LLM with multi-key pooling and retries.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

from aie.config import AIEConfig
from aie.knowledge.prompts import COMPONENT_ANALYSIS_PROMPT
from aie.knowledge.validator import LLMResponseValidator
from aie.models import DiscoveredComponent

logger = logging.getLogger(__name__)

try:
    from groq import Groq
    _GROQ_AVAILABLE = True
except ImportError:
    Groq = None  # type: ignore[assignment,misc]
    _GROQ_AVAILABLE = False


class GroqClientPool:
    """Manages a pool of Groq API clients across multiple API keys with round-robin dispatch and failover."""

    def __init__(self, api_keys: list[str]) -> None:
        self.clients: list[tuple[str, Any]] = []
        self._index = 0
        self._lock = threading.Lock()

        if _GROQ_AVAILABLE:
            for key in api_keys:
                clean_key = key.strip()
                if clean_key and not any(clean_key == k for k, _ in self.clients):
                    try:
                        masked = f"{clean_key[:6]}...{clean_key[-4:]}"
                        client = Groq(api_key=clean_key)
                        self.clients.append((masked, client))
                    except Exception as exc:
                        logger.warning("Failed to initialize Groq client for key: %s", exc)

    @property
    def is_available(self) -> bool:
        return len(self.clients) > 0

    @property
    def size(self) -> int:
        return len(self.clients)

    def get_client(self, offset: int = 0) -> tuple[str, Any] | tuple[None, None]:
        """Return a client from the pool using round-robin with attempt offset for failover."""
        if not self.clients:
            return None, None
        with self._lock:
            idx = (self._index + offset) % len(self.clients)
            self._index = (self._index + 1) % len(self.clients)
            return self.clients[idx]


class KnowledgeExtractor:
    """Extracts operational and architectural knowledge for a component via Groq API.
    
    Supports concurrent multi-key pooling across all keys configured in .env.
    """

    MAX_RETRIES: int = 3
    INITIAL_BACKOFF_SECONDS: float = 1.0
    TEMPERATURE: float = 0.1

    def __init__(self, config: AIEConfig | None = None) -> None:
        self.config = config or AIEConfig()
        self.model = getattr(self.config, "groq_model", "llama-3.3-70b-versatile")
        self.validator = LLMResponseValidator()

        # Collect all API keys (GROQ_API_KEY, GROQ_API_KEY1, GROQ_API_KEY2, etc.)
        api_keys = self.config.get_groq_api_keys() if hasattr(self.config, "get_groq_api_keys") else []
        self.pool = GroqClientPool(api_keys)

        if self.pool.is_available:
            logger.info("KnowledgeExtractor initialized with %d active Groq API keys", self.pool.size)
        else:
            if not _GROQ_AVAILABLE:
                logger.warning("Groq library not available in environment")
            else:
                logger.info("No Groq API keys found; KnowledgeExtractor will use heuristic knowledge")

    @property
    def pool_size(self) -> int:
        return self.pool.size

    @property
    def client(self) -> Any:
        _, c = self.pool.get_client()
        return c

    def extract(
        self,
        component: DiscoveredComponent,
        context: dict[str, str],
    ) -> dict[str, Any] | None:
        """Extract structured knowledge for a single component with multi-key failover and retries.

        Returns validated JSON dict conforming to COMPONENT_ANALYSIS_SCHEMA,
        or None if extraction fails or is unconfigured.
        """
        if not self.pool.is_available:
            logger.debug(
                "Skipping LLM knowledge extraction for %s: Groq client pool uninitialized",
                component.name,
            )
            return None

        prompt_text = self._build_prompt(context)
        component_id = component.component_id

        # Models to attempt: primary configured model, with standard Groq fallbacks
        candidate_models = [self.model]
        for fallback_model in ["llama-3.3-70b-versatile", "llama-3.1-8b-instant"]:
            if fallback_model not in candidate_models:
                candidate_models.append(fallback_model)

        backoff = self.INITIAL_BACKOFF_SECONDS
        total_attempts = max(self.MAX_RETRIES, self.pool.size)

        for attempt in range(1, total_attempts + 1):
            masked_key, client = self.pool.get_client(offset=attempt - 1)
            if not client:
                continue

            # Pick model (use primary on first attempt, fallbacks on subsequent if model failed)
            target_model = candidate_models[min(attempt - 1, len(candidate_models) - 1)]

            try:
                logger.info(
                    "Calling Groq LLM (model: %s, key: %s) for component '%s' (attempt %d/%d)...",
                    target_model,
                    masked_key,
                    component.name,
                    attempt,
                    total_attempts,
                )

                completion = client.chat.completions.create(
                    model=target_model,
                    messages=[
                        {
                            "role": "system",
                            "content": (
                                "You are a software architecture and operations analyst. "
                                "Output strictly valid JSON with no markdown preamble."
                            ),
                        },
                        {"role": "user", "content": prompt_text},
                    ],
                    temperature=self.TEMPERATURE,
                    response_format={"type": "json_object"},
                )

                raw_content = completion.choices[0].message.content
                if not raw_content:
                    logger.warning(
                        "Groq returned empty response content for component %s (key: %s)",
                        component.name,
                        masked_key,
                    )
                    continue

                validated = self.validator.validate(raw_content, component_id)
                if validated:
                    logger.info(
                        "Successfully extracted and validated knowledge for '%s' using key %s",
                        component.name,
                        masked_key,
                    )
                    return validated

                logger.warning(
                    "LLM response failed schema validation on attempt %d for %s",
                    attempt,
                    component.name,
                )

            except Exception as exc:
                err_str = str(exc).lower()
                logger.warning(
                    "Groq API error on attempt %d for component '%s' (key: %s): %s",
                    attempt,
                    component.name,
                    masked_key,
                    exc,
                )
                # If rate-limited (429), immediately switch to another key without heavy sleep
                if "429" in err_str or "rate limit" in err_str:
                    logger.info("Rate limit hit on key %s; failing over to next pool key immediately", masked_key)
                    continue

            if attempt < total_attempts:
                time.sleep(min(backoff, 2.0))
                backoff *= 1.5

        logger.error(
            "Failed all %d attempts to extract knowledge for component '%s'",
            total_attempts,
            component.name,
        )
        return None

    def _build_prompt(self, context: dict[str, str]) -> str:
        """Format prompt template safely with default empty strings."""
        return COMPONENT_ANALYSIS_PROMPT.format(
            component_name=context.get("component_name", ""),
            component_type=context.get("component_type", ""),
            technology=context.get("technology", ""),
            source_code_excerpts=context.get("source_code_excerpts", ""),
            configuration_excerpts=context.get("configuration_excerpts", ""),
            documentation_excerpts=context.get("documentation_excerpts", ""),
            related_components_summary=context.get("related_components_summary", ""),
            json_schema=context.get("json_schema", ""),
        )
