"""Intermediate Representation (IR) models for static code analysis."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class ImportInfo(BaseModel):
    """Represents a single import statement."""

    model_config = ConfigDict(frozen=True)

    module: str
    from_module: str | None = None
    name: str | None = None
    alias: str | None = None
    line: int
    is_relative: bool = False


class ClassInfo(BaseModel):
    """Represents a class definition."""

    model_config = ConfigDict(frozen=False)

    name: str
    bases: list[str] = Field(default_factory=list)
    methods: list[str] = Field(default_factory=list)
    decorators: list[str] = Field(default_factory=list)
    line: int
    end_line: int
    file: str


class CallInfo(BaseModel):
    """Represents a function/method call expression."""

    model_config = ConfigDict(frozen=True)

    callee: str
    line: int
    file: str


class FunctionInfo(BaseModel):
    """Represents a function or method definition."""

    model_config = ConfigDict(frozen=False)

    name: str
    is_async: bool = False
    params: list[str] = Field(default_factory=list)
    decorators: list[str] = Field(default_factory=list)
    return_annotation: str | None = None
    line: int
    end_line: int
    file: str
    calls: list[CallInfo] = Field(default_factory=list)


class FrameworkPattern(BaseModel):
    """Represents a detected framework-specific pattern."""

    model_config = ConfigDict(frozen=True)

    type: str  # e.g. "route", "orm", "cache", "queue"
    framework: str  # e.g. "fastapi", "sqlalchemy", "redis"
    details: dict = Field(default_factory=dict)
    line: int
    file: str


class FileIR(BaseModel):
    """Full intermediate representation for a single source file."""

    model_config = ConfigDict(frozen=False)

    file_path: str
    language: str
    imports: list[ImportInfo] = Field(default_factory=list)
    classes: list[ClassInfo] = Field(default_factory=list)
    functions: list[FunctionInfo] = Field(default_factory=list)
    global_calls: list[CallInfo] = Field(default_factory=list)
    framework_patterns: list[FrameworkPattern] = Field(default_factory=list)
    parse_error: bool = False


class CodebaseIR(BaseModel):
    """Aggregated intermediate representation for the entire codebase."""

    model_config = ConfigDict(frozen=False)

    files: list[FileIR] = Field(default_factory=list)
    total_classes: int = 0
    total_functions: int = 0
    total_imports: int = 0
