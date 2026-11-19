"""Python AST-based parser for static analysis."""

from __future__ import annotations

import ast
import logging
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


def _node_to_name(node: ast.expr) -> str:
    """Convert an AST expression node to a dotted name string."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return f"{_node_to_name(node.value)}.{node.attr}"
    if isinstance(node, ast.Call):
        return _node_to_name(node.func)
    if isinstance(node, ast.Subscript):
        return _node_to_name(node.value)
    return "<unknown>"


def _decorator_name(node: ast.expr) -> str:
    """Return string name for a decorator node."""
    return _node_to_name(node)


class PythonASTParser:
    """Parses a Python file using the built-in `ast` module.

    No code from the repository is executed; only AST traversal is performed.
    """

    # ------------------------------------------------------------------ #
    # Public API                                                           #
    # ------------------------------------------------------------------ #

    def parse_file(self, file_path: Path) -> FileIR:
        """Parse *file_path* and return a :class:`FileIR`.

        Returns a :class:`FileIR` with ``parse_error=True`` if the file
        contains a syntax error or cannot be read.  Files larger than 500 KB
        are skipped (returned as an empty FileIR).
        """
        file_str = str(file_path)

        try:
            stat = file_path.stat()
        except OSError as exc:
            logger.warning("Cannot stat %s: %s", file_path, exc)
            return FileIR(file_path=file_str, language="python", parse_error=True)

        if stat.st_size > MAX_FILE_SIZE:
            logger.debug("Skipping large file %s (%d bytes)", file_path, stat.st_size)
            return FileIR(file_path=file_str, language="python")

        try:
            source = file_path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            logger.warning("Cannot read %s: %s", file_path, exc)
            return FileIR(file_path=file_str, language="python", parse_error=True)

        try:
            tree = ast.parse(source, filename=file_str)
        except SyntaxError as exc:
            logger.debug("SyntaxError in %s: %s", file_path, exc)
            return FileIR(file_path=file_str, language="python", parse_error=True)
        except Exception as exc:  # pragma: no cover
            logger.warning("Unexpected parse error in %s: %s", file_path, exc)
            return FileIR(file_path=file_str, language="python", parse_error=True)

        imports = self._extract_imports(tree)
        classes = self._extract_classes(tree, file_str)
        functions = self._extract_functions(tree, file_str)
        global_calls = self._extract_calls(tree, file_str)
        framework_patterns = self._detect_patterns(tree, file_str)

        return FileIR(
            file_path=file_str,
            language="python",
            imports=imports,
            classes=classes,
            functions=functions,
            global_calls=global_calls,
            framework_patterns=framework_patterns,
        )

    # ------------------------------------------------------------------ #
    # Private helpers                                                      #
    # ------------------------------------------------------------------ #

    def _extract_imports(self, tree: ast.Module) -> list[ImportInfo]:
        """Walk the top-level tree and collect every import statement."""
        imports: list[ImportInfo] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports.append(
                        ImportInfo(
                            module=alias.name,
                            alias=alias.asname,
                            line=node.lineno,
                            is_relative=False,
                        )
                    )
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                is_relative = (node.level or 0) > 0
                for alias in node.names:
                    imports.append(
                        ImportInfo(
                            module=module,
                            from_module=module,
                            name=alias.name,
                            alias=alias.asname,
                            line=node.lineno,
                            is_relative=is_relative,
                        )
                    )
        return imports

    def _extract_classes(self, tree: ast.Module, file_str: str) -> list[ClassInfo]:
        """Extract all class definitions from the module."""
        classes: list[ClassInfo] = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            bases = [_node_to_name(b) for b in node.bases]
            methods = [
                n.name
                for n in ast.walk(node)
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                and n is not node  # skip self if somehow re-visited
            ]
            decorators = [_decorator_name(d) for d in node.decorator_list]
            end_line = getattr(node, "end_lineno", node.lineno)
            classes.append(
                ClassInfo(
                    name=node.name,
                    bases=bases,
                    methods=methods,
                    decorators=decorators,
                    line=node.lineno,
                    end_line=end_line,
                    file=file_str,
                )
            )
        return classes

    def _extract_functions(self, tree: ast.Module, file_str: str) -> list[FunctionInfo]:
        """Extract only top-level function definitions (not nested or methods)."""
        functions: list[FunctionInfo] = []
        for node in tree.body:  # top-level only
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            functions.append(self._build_function_info(node, file_str))
        return functions

    def _build_function_info(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
        file_str: str,
    ) -> FunctionInfo:
        is_async = isinstance(node, ast.AsyncFunctionDef)
        args = node.args
        params: list[str] = [a.arg for a in args.args]
        if args.vararg:
            params.append(f"*{args.vararg.arg}")
        if args.kwarg:
            params.append(f"**{args.kwarg.arg}")
        decorators = [_decorator_name(d) for d in node.decorator_list]
        ret_annotation: str | None = None
        if node.returns is not None:
            try:
                ret_annotation = ast.unparse(node.returns)
            except Exception:
                ret_annotation = None
        end_line = getattr(node, "end_lineno", node.lineno)
        # Gather calls inside this function body
        calls = [
            CallInfo(callee=_node_to_name(n.func), line=n.lineno, file=file_str)
            for n in ast.walk(node)
            if isinstance(n, ast.Call)
        ]
        return FunctionInfo(
            name=node.name,
            is_async=is_async,
            params=params,
            decorators=decorators,
            return_annotation=ret_annotation,
            line=node.lineno,
            end_line=end_line,
            file=file_str,
            calls=calls,
        )

    def _extract_calls(self, tree: ast.Module, file_str: str) -> list[CallInfo]:
        """Collect all call expressions in the module (global scope + nested)."""
        calls: list[CallInfo] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                try:
                    callee = _node_to_name(node.func)
                except Exception:
                    callee = "<unknown>"
                calls.append(CallInfo(callee=callee, line=node.lineno, file=file_str))
        return calls

    # ------------------------------------------------------------------ #
    # Framework pattern detection                                          #
    # ------------------------------------------------------------------ #

    _FASTAPI_ROUTE_METHODS = {"get", "post", "put", "patch", "delete", "head", "options"}
    _SQLALCHEMY_CALLS = {"create_engine", "sessionmaker", "declarative_base"}
    _REDIS_CALLS = {"Redis", "StrictRedis"}
    _REDIS_MODULE_CALLS = {"from_url"}  # redis.from_url(...)
    _AIOREDIS_CALLS = {"create_redis_pool"}
    _CELERY_CALLS = {"Celery"}
    _ASYNCPG_CALLS = {"connect", "create_pool"}
    _PSYCOPG2_CALLS = {"connect"}

    def _detect_patterns(self, tree: ast.Module, file_str: str) -> list[FrameworkPattern]:
        patterns: list[FrameworkPattern] = []

        # Track imported names so we can resolve framework membership
        import_map: dict[str, str] = {}  # local_name → module
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    local = alias.asname or alias.name.split(".")[0]
                    import_map[local] = alias.name
            elif isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                for alias in node.names:
                    local = alias.asname or alias.name
                    import_map[local] = mod

        for node in ast.walk(tree):
            # ----- Route decorators (FastAPI / Flask / Starlette) -----
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for dec in node.decorator_list:
                    dec_name = _decorator_name(dec)
                    # e.g. @app.get, @router.post, @blueprint.route
                    parts = dec_name.split(".")
                    if len(parts) >= 2 and parts[-1] in self._FASTAPI_ROUTE_METHODS:
                        fw = "fastapi"
                        # check if it looks more like Flask (blueprint)
                        root = import_map.get(parts[0], "")
                        if "flask" in root.lower():
                            fw = "flask"
                        patterns.append(
                            FrameworkPattern(
                                type="route",
                                framework=fw,
                                details={
                                    "method": parts[-1],
                                    "decorator": dec_name,
                                    "function": node.name,
                                },
                                line=dec.lineno if hasattr(dec, "lineno") else node.lineno,
                                file=file_str,
                            )
                        )
                    elif len(parts) >= 2 and parts[-1] in {"route", "add_url_rule"}:
                        patterns.append(
                            FrameworkPattern(
                                type="route",
                                framework="flask",
                                details={"decorator": dec_name, "function": node.name},
                                line=dec.lineno if hasattr(dec, "lineno") else node.lineno,
                                file=file_str,
                            )
                        )

            # ----- Call-based patterns -----
            if isinstance(node, ast.Call):
                callee = _node_to_name(node.func)
                parts = callee.split(".")
                func_name = parts[-1] if parts else ""
                root_name = parts[0] if parts else ""

                # SQLAlchemy
                if func_name in self._SQLALCHEMY_CALLS:
                    root_mod = import_map.get(root_name, root_name)
                    if "sqlalchemy" in root_mod.lower():
                        patterns.append(
                            FrameworkPattern(
                                type="orm",
                                framework="sqlalchemy",
                                details={"call": callee},
                                line=node.lineno,
                                file=file_str,
                            )
                        )

                # Redis direct constructor
                if func_name in self._REDIS_CALLS:
                    root_mod = import_map.get(root_name, root_name)
                    if "redis" in root_mod.lower():
                        patterns.append(
                            FrameworkPattern(
                                type="cache",
                                framework="redis",
                                details={"call": callee},
                                line=node.lineno,
                                file=file_str,
                            )
                        )

                # redis.from_url(...)
                if len(parts) >= 2 and func_name == "from_url":
                    root_mod = import_map.get(root_name, root_name)
                    if "redis" in root_mod.lower() or "redis" in root_name.lower():
                        patterns.append(
                            FrameworkPattern(
                                type="cache",
                                framework="redis",
                                details={"call": callee},
                                line=node.lineno,
                                file=file_str,
                            )
                        )

                # aioredis.create_redis_pool
                if func_name in self._AIOREDIS_CALLS:
                    root_mod = import_map.get(root_name, root_name)
                    if "aioredis" in root_mod.lower() or "aioredis" in root_name.lower():
                        patterns.append(
                            FrameworkPattern(
                                type="cache",
                                framework="redis",
                                details={"call": callee, "async": True},
                                line=node.lineno,
                                file=file_str,
                            )
                        )

                # Celery()
                if func_name in self._CELERY_CALLS:
                    root_mod = import_map.get(root_name, root_name)
                    if "celery" in root_mod.lower() or "celery" in root_name.lower():
                        patterns.append(
                            FrameworkPattern(
                                type="queue",
                                framework="celery",
                                details={"call": callee},
                                line=node.lineno,
                                file=file_str,
                            )
                        )

                # @celery.task decorator (also caught by function loop, but guard here)
                if len(parts) >= 2 and func_name == "task" and "celery" in root_name.lower():
                    patterns.append(
                        FrameworkPattern(
                            type="queue",
                            framework="celery",
                            details={"call": callee},
                            line=node.lineno,
                            file=file_str,
                        )
                    )

                # asyncpg
                if func_name in self._ASYNCPG_CALLS:
                    root_mod = import_map.get(root_name, root_name)
                    if "asyncpg" in root_mod.lower() or "asyncpg" in root_name.lower():
                        patterns.append(
                            FrameworkPattern(
                                type="database",
                                framework="asyncpg",
                                details={"call": callee},
                                line=node.lineno,
                                file=file_str,
                            )
                        )

                # psycopg2
                if func_name in self._PSYCOPG2_CALLS:
                    root_mod = import_map.get(root_name, root_name)
                    if "psycopg2" in root_mod.lower() or "psycopg2" in root_name.lower():
                        patterns.append(
                            FrameworkPattern(
                                type="database",
                                framework="psycopg2",
                                details={"call": callee},
                                line=node.lineno,
                                file=file_str,
                            )
                        )

        return patterns
