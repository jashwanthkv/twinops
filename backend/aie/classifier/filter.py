"""File filtering logic — decides which repository files are eligible for analysis."""

from __future__ import annotations

from pathlib import Path

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

EXCLUDED_DIRS: frozenset[str] = frozenset(
    {
        ".git",
        "node_modules",
        "venv",
        ".venv",
        "__pycache__",
        "dist",
        "build",
        ".next",
        ".nuxt",
        "target",
        ".gradle",
        "vendor",
        "bower_components",
    }
)

EXCLUDED_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".pyc",
        ".class",
        ".jar",
        ".war",
        ".so",
        ".dll",
        ".exe",
        ".bin",
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".svg",
        ".ico",
        ".woff",
        ".woff2",
        ".ttf",
        ".eot",
        ".mp4",
        ".mp3",
        ".wav",
        ".ogg",
        ".zip",
        ".tar",
        ".gz",
        ".bz2",
        ".xz",
        ".rar",
        ".7z",
        ".tgz",
    }
)

_BINARY_CHECK_BYTES = 8192


# ---------------------------------------------------------------------------
# FileFilter
# ---------------------------------------------------------------------------


class FileFilter:
    """Determines whether a given file path should be included in analysis."""

    def __init__(
        self,
        excluded_dirs: frozenset[str] | None = None,
        excluded_extensions: frozenset[str] | None = None,
        max_file_bytes: int = 10 * 1024 * 1024,
    ) -> None:
        self.excluded_dirs = excluded_dirs if excluded_dirs is not None else EXCLUDED_DIRS
        self.excluded_extensions = (
            excluded_extensions if excluded_extensions is not None else EXCLUDED_EXTENSIONS
        )
        self.max_file_bytes = max_file_bytes

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def should_include(self, path: Path | str) -> bool:
        """Return *True* if *path* should be included in the analysis.

        A file is **excluded** when:
        - Any component of its path is in :attr:`excluded_dirs`
        - Its extension is in :attr:`excluded_extensions`
        - It is a binary file (detected via null-byte scan)
        - It exceeds the configured size limit
        - It cannot be read (permissions, broken symlink, etc.)
        """
        p = Path(path)

        # Must be a regular file
        try:
            if not p.is_file():
                return False
        except OSError:
            return False

        # Check all path parts for excluded directory names
        for part in p.parts:
            if part in self.excluded_dirs:
                return False

        # Check extension (handle compound suffix like .tar.gz)
        suffix = p.suffix.lower()
        if suffix in self.excluded_extensions:
            return False

        # Also check compound suffixes such as .tar.gz
        name_lower = p.name.lower()
        for ext in self.excluded_extensions:
            if name_lower.endswith(ext):
                return False

        # Size check
        try:
            if p.stat().st_size > self.max_file_bytes:
                return False
        except OSError:
            return False

        # Binary check (most expensive — do last)
        if self.is_binary(p):
            return False

        return True

    @staticmethod
    def is_binary(path: Path | str) -> bool:
        """Return *True* if *path* appears to be a binary file.

        Uses a simple heuristic: read the first 8 192 bytes and look for null
        bytes, which are almost never present in well-formed text files.
        """
        try:
            with open(path, "rb") as fh:
                chunk = fh.read(_BINARY_CHECK_BYTES)
            return b"\x00" in chunk
        except OSError:
            # If we cannot read the file, treat it as binary to be safe
            return True
