"""Programming-language detection based on file extension."""

from __future__ import annotations

from pathlib import Path

# ---------------------------------------------------------------------------
# Extension → language mapping
# ---------------------------------------------------------------------------

EXTENSION_MAP: dict[str, str] = {
    # Python
    ".py": "python",
    ".pyw": "python",
    # JavaScript
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    # TypeScript
    ".ts": "typescript",
    ".tsx": "typescript",
    ".mts": "typescript",
    ".cts": "typescript",
    # Java
    ".java": "java",
    # Go
    ".go": "go",
    # Rust
    ".rs": "rust",
    # Ruby
    ".rb": "ruby",
    ".rake": "ruby",
    # PHP
    ".php": "php",
    ".phtml": "php",
    # C#
    ".cs": "csharp",
    # C / C++
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".cxx": "cpp",
    ".hpp": "cpp",
    ".hxx": "cpp",
    # Kotlin
    ".kt": "kotlin",
    ".kts": "kotlin",
    # Swift
    ".swift": "swift",
    # Scala
    ".scala": "scala",
    # Shell
    ".sh": "shell",
    ".bash": "shell",
    ".zsh": "shell",
    ".fish": "shell",
    # PowerShell
    ".ps1": "powershell",
    ".psm1": "powershell",
    # HTML
    ".html": "html",
    ".htm": "html",
    # CSS
    ".css": "css",
    ".scss": "scss",
    ".sass": "sass",
    ".less": "less",
    # SQL
    ".sql": "sql",
    # Markdown / docs (kept for completeness; classifier handles category)
    ".md": "markdown",
    ".rst": "rst",
    # YAML / TOML / JSON
    ".yaml": "yaml",
    ".yml": "yaml",
    ".toml": "toml",
    ".json": "json",
    # Terraform / HCL
    ".tf": "terraform",
    ".hcl": "terraform",
    # Dockerfile (no extension — handled via stem check below)
    # Makefile (no extension — handled via stem check below)
    # R
    ".r": "r",
    # Dart
    ".dart": "dart",
    # Elixir
    ".ex": "elixir",
    ".exs": "elixir",
    # Clojure
    ".clj": "clojure",
    ".cljs": "clojure",
    # Haskell
    ".hs": "haskell",
    # Lua
    ".lua": "lua",
    # Perl
    ".pl": "perl",
    ".pm": "perl",
    # Groovy
    ".groovy": "groovy",
    # XML
    ".xml": "xml",
    ".pom": "xml",
}

# Files whose language is determined by their stem (name without extension)
STEM_MAP: dict[str, str] = {
    "dockerfile": "dockerfile",
    "makefile": "makefile",
    "rakefile": "ruby",
    "gemfile": "ruby",
    "vagrantfile": "ruby",
    "jenkinsfile": "groovy",
    "procfile": "shell",
}


# ---------------------------------------------------------------------------
# LanguageDetector
# ---------------------------------------------------------------------------


class LanguageDetector:
    """Detects the programming language of a source file."""

    def __init__(
        self,
        extension_map: dict[str, str] | None = None,
        stem_map: dict[str, str] | None = None,
    ) -> None:
        self._ext_map: dict[str, str] = extension_map if extension_map is not None else EXTENSION_MAP
        self._stem_map: dict[str, str] = stem_map if stem_map is not None else STEM_MAP

    def detect(self, file_path: Path | str) -> str | None:
        """Return the language identifier for *file_path*, or *None* if unknown.

        Detection order:
        1. Extension match (case-insensitive)
        2. Stem match for files without an extension (e.g. ``Dockerfile``)
        """
        p = Path(file_path)
        suffix = p.suffix.lower()

        if suffix:
            lang = self._ext_map.get(suffix)
            if lang is not None:
                return lang

        # No extension (or unknown extension) — try the stem
        stem_lower = p.stem.lower()
        # Also try the full name in case there is no suffix at all (e.g. "Dockerfile")
        name_lower = p.name.lower()
        return self._stem_map.get(name_lower) or self._stem_map.get(stem_lower)
