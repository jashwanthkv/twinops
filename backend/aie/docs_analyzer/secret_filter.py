"""
SecretFilter — redacts secrets, tokens, credentials, and sensitive configurations.
"""

from __future__ import annotations

import re
from typing import Any


class SecretFilter:
    """Detects and redacts sensitive information such as passwords, API keys, and connection credentials."""

    SECRET_KEY_KEYWORDS: tuple[str, ...] = (
        "password",
        "passwd",
        "pwd",
        "secret",
        "token",
        "api_key",
        "apikey",
        "private_key",
        "credential",
        "auth",
        "authorization",
        "cert",
        "certificate",
        "signing_key",
        "access_key",
        "secret_key",
        "bearer",
    )

    SECRET_PATTERNS: list[re.Pattern] = [
        # Private key blocks
        re.compile(r"-----BEGIN\s+(?:RSA|DSA|EC|OPENSSH|PGP)?\s*PRIVATE\s+KEY-----[\s\S]*?-----END\s+(?:RSA|DSA|EC|OPENSSH|PGP)?\s*PRIVATE\s+KEY-----", re.MULTILINE),
        # Database connection strings with embedded passwords: scheme://user:pass@host
        re.compile(r"([a-zA-Z0-9+.-]+://[^:\s/@]+:)([^@\s]+)(@[^:\s/@]+)", re.IGNORECASE),
        # AWS Access Key IDs
        re.compile(r"\b(AKIA[0-9A-Z]{16})\b"),
        # AWS Secret Access Keys / generic high-entropy base64 secrets
        re.compile(r"(?i)\b(?:aws_secret_access_key|secret_key|api_key|token|password)\s*[:=]\s*['\"]?([a-zA-Z0-9/+=]{20,80})['\"]?"),
        # GitHub tokens (ghp_, gho_, ghu_, ghs_, ghr_)
        re.compile(r"\b(gh[pousr]_[A-Za-z0-9_]{36,255})\b"),
        # Generic bearer tokens
        re.compile(r"(?i)\bBearer\s+([A-Za-z0-9\-._~+/]+=*)"),
        # Slack tokens
        re.compile(r"\b(xox[baprs]-[0-9a-zA-Z]{10,48})\b"),
        # JWT tokens (3 base64url segments separated by dots)
        re.compile(r"\beyJ[A-Za-z0-9-_]+\.eyJ[A-Za-z0-9-_]+\.[A-Za-z0-9-_]+\b"),
    ]

    def is_secret_key(self, key: str) -> bool:
        """Returns True if the key name contains secret keywords (password, token, secret, etc.)."""
        if not key:
            return False
        cleaned = re.sub(r"[^a-zA-Z0-9]", "_", key.lower())
        return any(k in cleaned for k in self.SECRET_KEY_KEYWORDS)

    def filter_text(self, text: str) -> str:
        """Redacts detected secret patterns with [REDACTED]."""
        if not text:
            return text

        result = text

        # Handle URI user:password@host replacement safely
        def _redact_uri(m: re.Match) -> str:
            return f"{m.group(1)}[REDACTED]{m.group(3)}"

        result = self.SECRET_PATTERNS[1].sub(_redact_uri, result)

        # Handle specific assignment pattern
        def _redact_assignment(m: re.Match) -> str:
            full = m.group(0)
            secret_part = m.group(1)
            return full.replace(secret_part, "[REDACTED]")

        result = self.SECRET_PATTERNS[3].sub(_redact_assignment, result)

        # Handle generic tokens & keys
        for pattern in (self.SECRET_PATTERNS[0], self.SECRET_PATTERNS[2], self.SECRET_PATTERNS[4], self.SECRET_PATTERNS[5], self.SECRET_PATTERNS[6], self.SECRET_PATTERNS[7]):
            result = pattern.sub("[REDACTED]", result)

        return result

    def filter_dict(self, d: dict[str, Any]) -> dict[str, Any]:
        """Recursively traverses a dictionary and redacts secret keys or secret values."""
        if not isinstance(d, dict):
            return d

        sanitized: dict[str, Any] = {}
        for k, v in d.items():
            if self.is_secret_key(str(k)):
                sanitized[k] = "[REDACTED]"
            elif isinstance(v, dict):
                sanitized[k] = self.filter_dict(v)
            elif isinstance(v, list):
                sanitized[k] = self._filter_list(v)
            elif isinstance(v, str):
                sanitized[k] = self.filter_text(v)
            else:
                sanitized[k] = v
        return sanitized

    def _filter_list(self, lst: list[Any]) -> list[Any]:
        result = []
        for item in lst:
            if isinstance(item, dict):
                result.append(self.filter_dict(item))
            elif isinstance(item, list):
                result.append(self._filter_list(item))
            elif isinstance(item, str):
                result.append(self.filter_text(item))
            else:
                result.append(item)
        return result
