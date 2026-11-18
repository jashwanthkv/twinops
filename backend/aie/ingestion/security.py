"""Security checks for repository ingestion."""

from __future__ import annotations

import os
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_REPO_SIZE_BYTES: int = 500 * 1024 * 1024   # 500 MB
MAX_FILE_SIZE_BYTES: int = 10 * 1024 * 1024    # 10 MB

SECRET_FILENAMES: frozenset[str] = frozenset(
    {
        ".env",
        "id_rsa",
        "id_dsa",
        "id_ecdsa",
        "id_ed25519",
    }
)

SECRET_SUFFIXES: tuple[str, ...] = (
    ".pem",
    ".key",
    ".p12",
    ".pfx",
    ".cer",
    ".crt",
    ".keystore",
    ".jks",
)

SECRET_PREFIXES: tuple[str, ...] = (".env",)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class PathTraversalError(ValueError):
    """Raised when a path escapes the workspace root."""


class FileSizeError(ValueError):
    """Raised when a file exceeds the size limit."""


class RepoSizeError(ValueError):
    """Raised when a repository exceeds the size limit."""


# ---------------------------------------------------------------------------
# SecurityChecker
# ---------------------------------------------------------------------------


class SecurityChecker:
    """Provides security checks for the ingestion pipeline."""

    # ------------------------------------------------------------------
    # Path traversal
    # ------------------------------------------------------------------

    @staticmethod
    def check_path_traversal(path: Path | str, workspace_root: Path | str) -> None:
        """Raise :class:`PathTraversalError` if *path* escapes *workspace_root*.

        Both arguments are resolved to absolute paths before comparison so that
        relative segments (``..``) cannot be used to escape the root.
        """
        resolved_path = Path(path).resolve()
        resolved_root = Path(workspace_root).resolve()

        try:
            resolved_path.relative_to(resolved_root)
        except ValueError:
            raise PathTraversalError(
                f"Path '{resolved_path}' escapes workspace root '{resolved_root}'"
            )

    # ------------------------------------------------------------------
    # Secret file detection
    # ------------------------------------------------------------------

    @staticmethod
    def is_secret_file(filename: str) -> bool:
        """Return *True* if *filename* looks like a secret or credential file.

        Checks are case-insensitive and cover:
        - Exact matches: ``.env``, ``id_rsa``, ``id_dsa``, ``id_ecdsa``, ``id_ed25519``
        - Known secret extensions: ``.pem``, ``.key``, ``.p12``, ``.pfx``, etc.
        - Files that *start* with ``.env`` (e.g. ``.env.production``)
        """
        lower = filename.lower()
        bare = Path(lower).name  # strip any leading directory components

        if bare in SECRET_FILENAMES:
            return True

        for suffix in SECRET_SUFFIXES:
            if bare.endswith(suffix):
                return True

        for prefix in SECRET_PREFIXES:
            if bare.startswith(prefix):
                return True

        return False

    # ------------------------------------------------------------------
    # Size checks
    # ------------------------------------------------------------------

    @staticmethod
    def check_repo_size(path: Path | str) -> None:
        """Raise :class:`RepoSizeError` if the directory at *path* exceeds 500 MB.

        Walks the directory tree and accumulates total byte sizes.
        """
        root = Path(path)
        total = 0
        for dirpath, _dirnames, filenames in os.walk(root):
            for fname in filenames:
                try:
                    total += Path(dirpath, fname).stat().st_size
                except OSError:
                    pass  # skip unreadable files

                if total > MAX_REPO_SIZE_BYTES:
                    raise RepoSizeError(
                        f"Repository at '{root}' exceeds the 500 MB size limit "
                        f"(accumulated {total / 1024 / 1024:.1f} MB so far)"
                    )

    @staticmethod
    def check_file_size(path: Path | str) -> None:
        """Raise :class:`FileSizeError` if the file at *path* exceeds 10 MB."""
        file_path = Path(path)
        try:
            size = file_path.stat().st_size
        except OSError as exc:
            raise FileSizeError(f"Cannot stat file '{file_path}': {exc}") from exc

        if size > MAX_FILE_SIZE_BYTES:
            raise FileSizeError(
                f"File '{file_path}' is {size / 1024 / 1024:.1f} MB, "
                f"which exceeds the 10 MB limit"
            )

    # ------------------------------------------------------------------
    # Safe path resolution
    # ------------------------------------------------------------------

    @staticmethod
    def sanitize_workspace_path(path: Path | str, root: Path | str) -> Path:
        """Return the resolved, safe absolute path for *path* inside *root*.

        Raises :class:`PathTraversalError` if the resolved path escapes *root*.
        """
        resolved_root = Path(root).resolve()
        resolved_path = Path(path).resolve()

        try:
            resolved_path.relative_to(resolved_root)
        except ValueError:
            raise PathTraversalError(
                f"Path '{resolved_path}' escapes workspace root '{resolved_root}'"
            )

        return resolved_path
