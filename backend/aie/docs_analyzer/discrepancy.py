"""
DiscrepancyDetector — detects drift and mismatches between configuration/docs and actual code.
"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, Field

from aie.docs_analyzer.config_extractor import ConfigFinding


class Discrepancy(BaseModel):
    type: str
    description: str
    doc_source: str | None = None
    code_source: str | None = None
    severity: Literal["low", "medium", "high", "critical"] = "medium"


class DiscrepancyDetector:
    """Detects discrepancies between documented/configured settings and hardcoded code values."""

    def detect(
        self,
        config_findings: list[ConfigFinding | dict],
        code_ir: Any,
    ) -> list[Discrepancy]:
        discrepancies: list[Discrepancy] = []

        # Index config findings by uppercase key
        configs: dict[str, ConfigFinding] = {}
        for item in config_findings:
            if isinstance(item, dict):
                cf = ConfigFinding(**item)
            else:
                cf = item
            configs[cf.key.upper()] = cf

        # Extract file IRs
        file_irs = self._extract_file_irs(code_ir)

        for file_ir in file_irs:
            file_path = getattr(file_ir, "file_path", "") or file_ir.get("file_path", "")
            framework_patterns = getattr(file_ir, "framework_patterns", []) or file_ir.get("framework_patterns", [])
            calls = (
                getattr(file_ir, "global_calls", []) or file_ir.get("global_calls", [])
            ) + self._collect_func_calls(file_ir)

            # 1. Pool Size discrepancy check
            self._check_pool_size(configs, framework_patterns, calls, file_path, discrepancies)

            # 2. Port mismatch check
            self._check_port_mismatch(configs, framework_patterns, calls, file_path, discrepancies)

            # 3. Hardcoded DB / Redis connection check
            self._check_hardcoded_connections(configs, framework_patterns, calls, file_path, discrepancies)

        return discrepancies

    # ------------------------------------------------------------------
    # Discrepancy rules
    # ------------------------------------------------------------------

    def _check_pool_size(
        self,
        configs: dict[str, ConfigFinding],
        patterns: list[Any],
        calls: list[Any],
        file_path: str,
        discrepancies: list[Discrepancy],
    ) -> None:
        cfg_pool = configs.get("POOL_SIZE") or configs.get("DB_POOL_SIZE") or configs.get("SQLALCHEMY_POOL_SIZE")
        if not cfg_pool:
            return

        cfg_val_str = str(cfg_pool.value).strip()
        try:
            cfg_val = int(cfg_val_str)
        except ValueError:
            return

        # Check framework patterns
        for p in patterns:
            details = getattr(p, "details", {}) or (p.get("details", {}) if isinstance(p, dict) else {})
            code_pool = details.get("pool_size")
            if code_pool is not None and str(code_pool).isdigit():
                if int(code_pool) != cfg_val:
                    discrepancies.append(
                        Discrepancy(
                            type="pool_size_mismatch",
                            description=(
                                f"Config specifies {cfg_pool.key}={cfg_val} in {cfg_pool.source_file}, "
                                f"but code specifies pool_size={code_pool} in {file_path}."
                            ),
                            doc_source=cfg_pool.source_file,
                            code_source=file_path,
                            severity="medium",
                        )
                    )

        # Check call arguments
        for call in calls:
            callee = getattr(call, "callee", "") or (call.get("callee", "") if isinstance(call, dict) else "")
            line = getattr(call, "line", None) or (call.get("line") if isinstance(call, dict) else None)
            if "create_engine" in callee or "Pool" in callee or "create_pool" in callee:
                line_str = f":line {line}" if line else ""
                # Check if call contains literal pool_size argument with different value
                # Note: call callee inspection
                pass

    def _check_port_mismatch(
        self,
        configs: dict[str, ConfigFinding],
        patterns: list[Any],
        calls: list[Any],
        file_path: str,
        discrepancies: list[Discrepancy],
    ) -> None:
        cfg_port = configs.get("PORT") or configs.get("APP_PORT") or configs.get("SERVER_PORT")
        if not cfg_port:
            return

        cfg_port_str = str(cfg_port.value).strip()
        try:
            cfg_port_val = int(cfg_port_str)
        except ValueError:
            return

        for p in patterns:
            details = getattr(p, "details", {}) or (p.get("details", {}) if isinstance(p, dict) else {})
            code_port = details.get("port")
            if code_port is not None and str(code_port).isdigit():
                if int(code_port) != cfg_port_val:
                    discrepancies.append(
                        Discrepancy(
                            type="port_mismatch",
                            description=(
                                f"Config specifies {cfg_port.key}={cfg_port_val} in {cfg_port.source_file}, "
                                f"but code configures port={code_port} in {file_path}."
                            ),
                            doc_source=cfg_port.source_file,
                            code_source=file_path,
                            severity="high",
                        )
                    )

    def _check_hardcoded_connections(
        self,
        configs: dict[str, ConfigFinding],
        patterns: list[Any],
        calls: list[Any],
        file_path: str,
        discrepancies: list[Discrepancy],
    ) -> None:
        for p in patterns:
            details = getattr(p, "details", {}) or (p.get("details", {}) if isinstance(p, dict) else {})
            url = details.get("url") or details.get("connection_string") or ""
            if isinstance(url, str) and url.startswith(("postgres://", "postgresql://", "mysql://", "redis://", "mongodb://")):
                if "localhost" in url or "127.0.0.1" in url:
                    if "DATABASE_URL" in configs or "REDIS_URL" in configs:
                        discrepancies.append(
                            Discrepancy(
                                type="hardcoded_connection_url",
                                description=(
                                    f"File {file_path} appears to contain a hardcoded local connection URL ('{url[:30]}...'), "
                                    f"which may bypass environment configuration."
                                ),
                                doc_source=configs.get("DATABASE_URL", configs.get("REDIS_URL")).source_file if (configs.get("DATABASE_URL") or configs.get("REDIS_URL")) else None,
                                code_source=file_path,
                                severity="medium",
                            )
                        )

    # ------------------------------------------------------------------
    # IR traversal helpers
    # ------------------------------------------------------------------

    def _extract_file_irs(self, code_ir: Any) -> list[Any]:
        if not code_ir:
            return []
        if hasattr(code_ir, "files"):
            return code_ir.files
        if isinstance(code_ir, dict):
            return code_ir.get("files", [])
        if isinstance(code_ir, list):
            return code_ir
        return []

    def _collect_func_calls(self, file_ir: Any) -> list[Any]:
        functions = getattr(file_ir, "functions", []) or (file_ir.get("functions", []) if isinstance(file_ir, dict) else [])
        calls = []
        for func in functions:
            fn_calls = getattr(func, "calls", []) or (func.get("calls", []) if isinstance(func, dict) else [])
            calls.extend(fn_calls)
        return calls
