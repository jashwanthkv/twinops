"""Regex-based parser for JavaScript and TypeScript source files."""

from __future__ import annotations

import logging
import re
from pathlib import Path

from aie.static_analysis.ir import (
    CallInfo,
    ClassInfo,
    FileIR,
    FrameworkPattern,
    FunctionInfo,
    ImportInfo,
)

logger = logging.getLogger(__name__)

MAX_FILE_SIZE = 500 * 1024  # 500 KB


class JSRegexParser:
    """Parses JavaScript and TypeScript files using regular expressions.

    Does not execute any repository code.
    """

    # --- Regex patterns ---
    # Imports: import x from 'y', import { x } from 'y', import * as x from 'y'
    _RE_ES_IMPORT = re.compile(
        r"""import\s+(?:(?P<name>[A-Za-z0-9_$,\s{}*]+)\s+from\s+)?['"](?P<module>[^'"]+)['"]""",
        re.MULTILINE,
    )
    # Require: const x = require('y'), const { x } = require('y')
    _RE_REQUIRE = re.compile(
        r"""(?:(?:const|let|var)\s+(?P<name>[A-Za-z0-9_$,\s{}]+)\s*=\s*)?require\s*\(\s*['"](?P<module>[^'"]+)['"]\s*\)""",
        re.MULTILINE,
    )
    # Classes: class Foo extends Bar
    _RE_CLASS = re.compile(
        r"""class\s+(?P<name>[A-Za-z0-9_$]+)(?:\s+extends\s+(?P<base>[A-Za-z0-9_$.]+))?""",
        re.MULTILINE,
    )
    # Async functions: async function foo(...) / async (args) => / async foo => / async method(...) {
    _RE_ASYNC_FUNC = re.compile(
        r"""(?:async\s+function\s*(?P<name1>[A-Za-z0-9_$]+)?\s*\((?P<params1>[^)]*)\)|async\s*\((?P<params2>[^)]*)\)\s*=>|async\s+(?P<param3>[A-Za-z0-9_$]+)\s*=>|async\s+(?P<name2>[A-Za-z0-9_$]+)\s*\((?P<params3>[^)]*)\)\s*\{)""",
        re.MULTILINE,
    )
    # Regular functions: function foo(...)
    _RE_SYNC_FUNC = re.compile(
        r"""function\s+(?P<name>[A-Za-z0-9_$]+)\s*\((?P<params>[^)]*)\)""",
        re.MULTILINE,
    )
    # Express routes: (app|router).(get|post|put|delete|patch|all|use)('path', ...)
    _RE_EXPRESS_ROUTE = re.compile(
        r"""(?P<router>app|router|\w+[Rr]outer)\s*\.\s*(?P<method>get|post|put|delete|patch|all|use)\s*\(\s*['"`](?P<path>[^'"`]+)['"`]""",
        re.MULTILINE,
    )
    # Redis: new Redis(), createClient(), redis.createClient()
    _RE_REDIS = re.compile(
        r"""(?:new\s+Redis\s*\(|(?:\w+\.)?createClient\s*\()""",
        re.MULTILINE,
    )
    # Database clients: pg Pool, mongoose.connect, sequelize
    _RE_PG_POOL = re.compile(r"""new\s+Pool\s*\(""", re.MULTILINE)
    _RE_MONGOOSE = re.compile(r"""mongoose\s*\.\s*connect\s*\(""", re.MULTILINE)
    _RE_SEQUELIZE = re.compile(r"""new\s+Sequelize\s*\(""", re.MULTILINE)

    def parse_file(self, file_path: Path) -> FileIR:
        """Parse *file_path* using regex heuristics and return FileIR."""
        file_str = str(file_path)
        ext = file_path.suffix.lower()
        lang = "typescript" if ext in {".ts", ".tsx"} else "javascript"

        try:
            stat = file_path.stat()
        except OSError as exc:
            logger.warning("Cannot stat %s: %s", file_path, exc)
            return FileIR(file_path=file_str, language=lang, parse_error=True)

        if stat.st_size > MAX_FILE_SIZE:
            logger.debug("Skipping large file %s (%d bytes)", file_path, stat.st_size)
            return FileIR(file_path=file_str, language=lang)

        try:
            content = file_path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            logger.warning("Cannot read %s: %s", file_path, exc)
            return FileIR(file_path=file_str, language=lang, parse_error=True)

        try:
            imports = self._extract_imports(content)
            classes = self._extract_classes(content, file_str)
            functions = self._extract_functions(content, file_str)
            global_calls = self._extract_calls(content, file_str)
            framework_patterns = self._detect_patterns(content, file_str)

            return FileIR(
                file_path=file_str,
                language=lang,
                imports=imports,
                classes=classes,
                functions=functions,
                global_calls=global_calls,
                framework_patterns=framework_patterns,
            )
        except Exception as exc:
            logger.warning("Error parsing JS/TS file %s: %s", file_path, exc)
            return FileIR(file_path=file_str, language=lang, parse_error=True)

    def _line_number(self, content: str, char_index: int) -> int:
        """Calculate 1-based line number from character index."""
        return content[:char_index].count("\n") + 1

    def _extract_imports(self, content: str) -> list[ImportInfo]:
        imports: list[ImportInfo] = []

        # ES imports
        for match in self._RE_ES_IMPORT.finditer(content):
            module = match.group("module")
            name = match.group("name")
            line = self._line_number(content, match.start())
            is_relative = module.startswith(("./", "../", "."))
            imports.append(
                ImportInfo(
                    module=module,
                    from_module=module if name else None,
                    name=name.strip() if name else None,
                    line=line,
                    is_relative=is_relative,
                )
            )

        # CommonJS require
        for match in self._RE_REQUIRE.finditer(content):
            module = match.group("module")
            name = match.group("name")
            line = self._line_number(content, match.start())
            is_relative = module.startswith(("./", "../", "."))
            imports.append(
                ImportInfo(
                    module=module,
                    from_module=module,
                    name=name.strip() if name else None,
                    line=line,
                    is_relative=is_relative,
                )
            )

        return imports

    def _extract_classes(self, content: str, file_str: str) -> list[ClassInfo]:
        classes: list[ClassInfo] = []
        for match in self._RE_CLASS.finditer(content):
            name = match.group("name")
            base = match.group("base")
            line = self._line_number(content, match.start())
            bases = [base.strip()] if base else []
            classes.append(
                ClassInfo(
                    name=name,
                    bases=bases,
                    methods=[],
                    decorators=[],
                    line=line,
                    end_line=line,
                    file=file_str,
                )
            )
        return classes

    def _extract_functions(self, content: str, file_str: str) -> list[FunctionInfo]:
        functions: list[FunctionInfo] = []

        # Async functions
        for match in self._RE_ASYNC_FUNC.finditer(content):
            name = match.group("name1") or match.group("name2") or "anonymous_async"
            raw_params = match.group("params1") or match.group("params2") or match.group("params3") or match.group("param3") or ""
            params = [p.strip() for p in raw_params.split(",") if p.strip()]
            line = self._line_number(content, match.start())
            functions.append(
                FunctionInfo(
                    name=name,
                    is_async=True,
                    params=params,
                    decorators=[],
                    line=line,
                    end_line=line,
                    file=file_str,
                    calls=[],
                )
            )

        # Regular functions
        for match in self._RE_SYNC_FUNC.finditer(content):
            name = match.group("name")
            raw_params = match.group("params") or ""
            params = [p.strip() for p in raw_params.split(",") if p.strip()]
            line = self._line_number(content, match.start())
            functions.append(
                FunctionInfo(
                    name=name,
                    is_async=False,
                    params=params,
                    decorators=[],
                    line=line,
                    end_line=line,
                    file=file_str,
                    calls=[],
                )
            )

        return functions

    def _extract_calls(self, content: str, file_str: str) -> list[CallInfo]:
        calls: list[CallInfo] = []
        # General method or function call regex: identifier(...)
        call_re = re.compile(r"""\b([A-Za-z0-9_$.]+)\s*\(""", re.MULTILINE)
        for match in call_re.finditer(content):
            callee = match.group(1)
            # Skip keywords
            if callee in {"if", "for", "while", "switch", "catch", "function", "require", "import"}:
                continue
            line = self._line_number(content, match.start())
            calls.append(CallInfo(callee=callee, line=line, file=file_str))
        return calls

    def _detect_patterns(self, content: str, file_str: str) -> list[FrameworkPattern]:
        patterns: list[FrameworkPattern] = []

        # Express routes
        for match in self._RE_EXPRESS_ROUTE.finditer(content):
            router = match.group("router")
            method = match.group("method")
            path = match.group("path")
            line = self._line_number(content, match.start())
            patterns.append(
                FrameworkPattern(
                    type="route",
                    framework="express",
                    details={"router": router, "method": method, "path": path},
                    line=line,
                    file=file_str,
                )
            )

        # Redis client
        for match in self._RE_REDIS.finditer(content):
            line = self._line_number(content, match.start())
            patterns.append(
                FrameworkPattern(
                    type="cache",
                    framework="redis",
                    details={"call": match.group(0)},
                    line=line,
                    file=file_str,
                )
            )

        # pg Pool
        for match in self._RE_PG_POOL.finditer(content):
            line = self._line_number(content, match.start())
            patterns.append(
                FrameworkPattern(
                    type="database",
                    framework="pg",
                    details={"call": match.group(0)},
                    line=line,
                    file=file_str,
                )
            )

        # Mongoose
        for match in self._RE_MONGOOSE.finditer(content):
            line = self._line_number(content, match.start())
            patterns.append(
                FrameworkPattern(
                    type="database",
                    framework="mongoose",
                    details={"call": match.group(0)},
                    line=line,
                    file=file_str,
                )
            )

        # Sequelize
        for match in self._RE_SEQUELIZE.finditer(content):
            line = self._line_number(content, match.start())
            patterns.append(
                FrameworkPattern(
                    type="orm",
                    framework="sequelize",
                    details={"call": match.group(0)},
                    line=line,
                    file=file_str,
                )
            )

        return patterns
