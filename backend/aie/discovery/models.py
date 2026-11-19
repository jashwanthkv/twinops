"""
Pydantic v2 models for Project Discovery (Module 3).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class LanguageInfo(BaseModel):
    name: str
    version: str | None = None
    file_count: int = 0
    percentage: float = 0.0


class FrameworkInfo(BaseModel):
    name: str
    version: str | None = None
    evidence_file: str | None = None
    confidence: float = 1.0


class TechnologyInfo(BaseModel):
    name: str
    version: str | None = None
    detected_from: str | None = None
    category: str | None = None


class EntryPoint(BaseModel):
    file: str
    type: Literal["main", "server", "worker", "cli"]


class TechnologyProfile(BaseModel):
    run_id: str | None = None
    languages: list[LanguageInfo] = Field(default_factory=list)
    frameworks: list[FrameworkInfo] = Field(default_factory=list)
    databases: list[TechnologyInfo] = Field(default_factory=list)
    caches: list[TechnologyInfo] = Field(default_factory=list)
    message_brokers: list[TechnologyInfo] = Field(default_factory=list)
    web_servers: list[TechnologyInfo] = Field(default_factory=list)
    infrastructure: list[TechnologyInfo] = Field(default_factory=list)
    dependency_managers: list[TechnologyInfo] = Field(default_factory=list)
    entry_points: list[EntryPoint] = Field(default_factory=list)
    primary_language: str | None = None
    primary_framework: str | None = None
    language_primary_frameworks: dict[str, str] = Field(default_factory=dict)
    technologies: list[TechnologyInfo] = Field(default_factory=list)
