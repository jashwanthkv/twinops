"""Builds an inventory of all analysable files in a workspace."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import TYPE_CHECKING

from aie.models import FileEntry, FileCategory

if TYPE_CHECKING:
    from aie.classifier.classifier import FileClassifier
    from aie.classifier.filter import FileFilter
    from aie.classifier.language_detector import LanguageDetector
    from aie.ingestion.manager import AnalysisWorkspace

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_HASH_CHUNK = 65_536  # 64 KiB read chunks for SHA-256 computation


# ---------------------------------------------------------------------------
# InventoryBuilder
# ---------------------------------------------------------------------------


class InventoryBuilder:
    """Walks a workspace directory and produces a list of :class:`~aie.models.FileEntry` records."""

    def build(
        self,
        workspace: "AnalysisWorkspace",
        file_filter: "FileFilter",
        classifier: "FileClassifier",
        language_detector: "LanguageDetector",
    ) -> list[FileEntry]:
        """Scan *workspace* and return classified file entries.

        Parameters
        ----------
        workspace:
            Cloned repository workspace to scan.
        file_filter:
            Decides whether a file should be included.
        classifier:
            Assigns a :class:`~aie.models.FileCategory` to each included file.
        language_detector:
            Identifies the programming language of each included file.

        Returns
        -------
        list[FileEntry]
            One entry per included file, with SHA-256 hash, size, language,
            category, and workspace-relative path.
        """
        root: Path = workspace.root_path
        entries: list[FileEntry] = []

        for dirpath, dirnames, filenames in os.walk(root):
            dir_path = Path(dirpath)

            # Prune excluded directories in-place so os.walk skips them
            dirnames[:] = [
                d for d in dirnames
                if d not in file_filter.excluded_dirs
            ]

            for filename in filenames:
                abs_path = dir_path / filename

                # Apply filter
                if not file_filter.should_include(abs_path):
                    continue

                # Relative path from workspace root
                try:
                    rel_path = abs_path.relative_to(root)
                except ValueError:
                    continue  # should not happen, but skip safely

                # File size
                try:
                    size_bytes = abs_path.stat().st_size
                except OSError:
                    continue

                # Language detection
                language = language_detector.detect(abs_path)

                # File classification
                try:
                    category = classifier.classify(abs_path, language)
                except Exception:
                    category = FileCategory.SOURCE_CODE

                # SHA-256 hash
                sha256 = self._compute_sha256(abs_path)
                if sha256 is None:
                    continue  # unreadable file — skip

                entry = FileEntry(
                    path=str(rel_path),
                    language=language,
                    category=category,
                    size_bytes=size_bytes,
                    sha256=sha256,
                )
                entries.append(entry)

        return entries

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_sha256(path: Path) -> str | None:
        """Compute the SHA-256 hex-digest of *path*.

        Returns *None* if the file cannot be read.
        """
        h = hashlib.sha256()
        try:
            with open(path, "rb") as fh:
                while chunk := fh.read(_HASH_CHUNK):
                    h.update(chunk)
        except OSError:
            return None
        return h.hexdigest()
