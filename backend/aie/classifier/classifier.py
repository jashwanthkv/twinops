"""File category classifier for the AIE analysis pipeline."""

from __future__ import annotations

from pathlib import Path

from aie.models import FileCategory

# ---------------------------------------------------------------------------
# Well-known dependency manifest filenames
# ---------------------------------------------------------------------------

DEPENDENCY_MANIFESTS: frozenset[str] = frozenset(
    {
        "requirements.txt",
        "requirements-dev.txt",
        "requirements-test.txt",
        "package.json",
        "package-lock.json",
        "yarn.lock",
        "pnpm-lock.yaml",
        "pom.xml",
        "build.gradle",
        "build.gradle.kts",
        "gradle.lockfile",
        "Gemfile",
        "Gemfile.lock",
        "Cargo.toml",
        "Cargo.lock",
        "go.mod",
        "go.sum",
        "composer.json",
        "composer.lock",
        "pyproject.toml",
        "setup.py",
        "setup.cfg",
        "Pipfile",
        "Pipfile.lock",
        "poetry.lock",
        "nuget.config",
        ".csproj",
        "packages.config",
    }
)

CONFIGURATION_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".yaml",
        ".yml",
        ".toml",
        ".ini",
        ".cfg",
        ".conf",
        ".config",
        ".json",
        ".env",
        ".properties",
        ".xml",
    }
)

# Configuration files that should NOT override a more specific category
_DEPENDENCY_EXTENSIONS: frozenset[str] = frozenset({".toml"})

# Infrastructure-related name patterns
_INFRA_STEMS: tuple[str, ...] = (
    "dockerfile",
    "docker-compose",
    "docker-compose.override",
)

_INFRA_EXTENSIONS: frozenset[str] = frozenset({".tf", ".hcl"})

_INFRA_DIRS: frozenset[str] = frozenset({".k8s", "k8s", "kubernetes", "terraform", "infra", "infrastructure"})

# Database-related
_DATABASE_EXTENSIONS: frozenset[str] = frozenset({".sql"})
_DATABASE_DIRS: frozenset[str] = frozenset({"migrations", "migration", "db", "database", "schema"})

# Documentation extensions / directories
_DOC_EXTENSIONS: frozenset[str] = frozenset({".md", ".rst", ".txt", ".adoc", ".asciidoc"})
_DOC_DIRS: frozenset[str] = frozenset({"docs", "doc", "documentation", "wiki"})

# Test indicators
_TEST_PREFIXES: tuple[str, ...] = ("test_", "tests_")
_TEST_SUFFIXES: tuple[str, ...] = ("_test", "_tests", "_spec", ".spec", ".test")
_TEST_DIRS: frozenset[str] = frozenset({"tests", "test", "__tests__", "spec", "specs", "e2e"})


# ---------------------------------------------------------------------------
# FileClassifier
# ---------------------------------------------------------------------------


class FileClassifier:
    """Classifies a file into a :class:`~aie.models.FileCategory`."""

    def classify(self, file_path: Path | str, language: str | None = None) -> FileCategory:
        """Return the :class:`~aie.models.FileCategory` that best describes *file_path*.

        Parameters
        ----------
        file_path:
            Absolute or relative path to the file.
        language:
            Pre-detected language string (from :class:`~aie.classifier.language_detector.LanguageDetector`).
            Used to determine whether a file is *source code* at the end.
        """
        p = Path(file_path)
        name_lower = p.name.lower()
        stem_lower = p.stem.lower()
        suffix_lower = p.suffix.lower()
        parts_lower = {part.lower() for part in p.parts}

        # ----------------------------------------------------------------
        # 1. Test files
        # ----------------------------------------------------------------
        if self._is_test(stem_lower, name_lower, parts_lower):
            return FileCategory.TEST

        # ----------------------------------------------------------------
        # 2. Infrastructure
        # ----------------------------------------------------------------
        if self._is_infrastructure(name_lower, stem_lower, suffix_lower, parts_lower):
            return FileCategory.INFRASTRUCTURE

        # ----------------------------------------------------------------
        # 3. Database schema / migrations
        # ----------------------------------------------------------------
        if self._is_database(suffix_lower, parts_lower):
            return FileCategory.DATABASE_SCHEMA

        # ----------------------------------------------------------------
        # 4. Dependency manifests (checked before generic configuration)
        # ----------------------------------------------------------------
        if name_lower in {m.lower() for m in DEPENDENCY_MANIFESTS}:
            return FileCategory.DEPENDENCY_MANIFEST

        # ----------------------------------------------------------------
        # 5. Documentation
        # ----------------------------------------------------------------
        if self._is_documentation(suffix_lower, parts_lower):
            return FileCategory.DOCUMENTATION

        # ----------------------------------------------------------------
        # 6. Configuration
        # ----------------------------------------------------------------
        if self._is_configuration(suffix_lower, name_lower):
            return FileCategory.CONFIGURATION

        # ----------------------------------------------------------------
        # 7. Source code (fallback when a language was detected)
        # ----------------------------------------------------------------
        if language is not None:
            return FileCategory.SOURCE_CODE

        # ----------------------------------------------------------------
        # 8. Unknown
        # ----------------------------------------------------------------
        return FileCategory.SOURCE_CODE  # treat unknown as source rather than dropping

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _is_test(stem: str, name: str, parts: set[str]) -> bool:
        # Directory-level test detection
        if parts & _TEST_DIRS:
            return True

        # Prefix: test_foo.py
        for prefix in _TEST_PREFIXES:
            if stem.startswith(prefix):
                return True

        # Suffix: foo_test.py, foo.spec.ts
        for suffix in _TEST_SUFFIXES:
            if stem.endswith(suffix) or name.endswith(suffix + "."):
                return True
            # Handle double-dot like foo.spec.ts → stem=foo.spec
            if f"{suffix}." in name:
                return True

        return False

    @staticmethod
    def _is_infrastructure(name: str, stem: str, suffix: str, parts: set[str]) -> bool:
        # Exact stem match (Dockerfile, docker-compose*)
        for infra_stem in _INFRA_STEMS:
            if name.startswith(infra_stem):
                return True

        # Dockerfile.prod etc.
        if stem.lower().startswith("dockerfile"):
            return True

        # Terraform / HCL extension
        if suffix in _INFRA_EXTENSIONS:
            return True

        # Directory-based detection
        if parts & _INFRA_DIRS:
            return True

        return False

    @staticmethod
    def _is_database(suffix: str, parts: set[str]) -> bool:
        if suffix in _DATABASE_EXTENSIONS:
            return True
        if parts & _DATABASE_DIRS:
            return True
        return False

    @staticmethod
    def _is_documentation(suffix: str, parts: set[str]) -> bool:
        if suffix in _DOC_EXTENSIONS:
            # Only classify as docs if it's a doc extension AND either lives in
            # a docs directory, OR is .md/.rst (which are almost always docs)
            if parts & _DOC_DIRS:
                return True
            if suffix in {".md", ".rst", ".adoc", ".asciidoc"}:
                return True
        return False

    @staticmethod
    def _is_configuration(suffix: str, name: str) -> bool:
        # JSON files named package.json are DEPENDENCY_MANIFEST (already caught above)
        if suffix == ".json" and name in {"package.json", "package-lock.json"}:
            return False
        return suffix in CONFIGURATION_EXTENSIONS
