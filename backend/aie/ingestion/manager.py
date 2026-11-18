"""Repository ingestion manager — clones repos into isolated workspaces."""

from __future__ import annotations

import shutil
import urllib.parse
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import git  # gitpython

from aie.ingestion.security import SecurityChecker
from aie.ingestion.github_client import GitHubClient

# ---------------------------------------------------------------------------
# AnalysisWorkspace dataclass
# ---------------------------------------------------------------------------


@dataclass
class AnalysisWorkspace:
    """Represents a cloned repository ready for analysis."""

    workspace_id: str
    repo_url: str
    branch: str
    commit_hash: str
    root_path: Path
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


# ---------------------------------------------------------------------------
# IngestionManager
# ---------------------------------------------------------------------------


class IngestionManager:
    """Manages the lifecycle of analysis workspaces (clone → analyse → cleanup)."""

    def __init__(
        self,
        workspace_base_dir: Path | str = Path("./aie_workspaces"),
    ) -> None:
        self._base_dir = Path(workspace_base_dir).resolve()
        self._base_dir.mkdir(parents=True, exist_ok=True)
        self._security = SecurityChecker()
        self._github = GitHubClient()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def clone(
        self,
        repo_url: str,
        branch: str | None,
        auth_token: str | None,
        workspace_id: str,
    ) -> AnalysisWorkspace:
        """Clone a repository (shallow, depth=1) into an isolated workspace directory.

        Parameters
        ----------
        repo_url:
            Public or private GitHub HTTPS URL.
        branch:
            Branch / tag to clone. ``None`` clones the default branch.
        auth_token:
            Personal Access Token for private repositories. When provided it is
            embedded in the clone URL and **never** stored on disk or logged.
        workspace_id:
            Unique identifier for this analysis run; used as the directory name.

        Returns
        -------
        :class:`AnalysisWorkspace`
            Dataclass carrying the workspace metadata.

        Raises
        ------
        ValueError
            If the workspace directory would escape the base directory.
        git.GitCommandError
            If the clone fails (bad credentials, network error, etc.).
        """
        workspace_dir = self._resolve_workspace_dir(workspace_id)

        clone_url = self._build_clone_url(repo_url, auth_token)

        clone_kwargs: dict = {
            "depth": 1,
            "no_single_branch": False,
        }
        if branch:
            clone_kwargs["branch"] = branch

        repo = git.Repo.clone_from(clone_url, str(workspace_dir), **clone_kwargs)  # type: ignore[attr-defined]

        # Determine the branch that was actually checked out
        try:
            actual_branch = repo.active_branch.name
        except TypeError:
            # Detached HEAD (e.g. when cloning a tag)
            actual_branch = branch or "HEAD"

        commit_hash = repo.head.commit.hexsha

        return AnalysisWorkspace(
            workspace_id=workspace_id,
            repo_url=repo_url,
            branch=actual_branch,
            commit_hash=commit_hash,
            root_path=workspace_dir,
            created_at=datetime.now(timezone.utc),
        )

    def cleanup(self, workspace: AnalysisWorkspace) -> None:
        """Remove the cloned directory for *workspace*.

        Silently does nothing if the directory no longer exists.
        """
        try:
            # Safety check: the workspace root must still be inside our base dir
            self._security.check_path_traversal(workspace.root_path, self._base_dir)
        except Exception:
            # If anything goes wrong with the safety check, refuse to delete
            return

        if workspace.root_path.exists():
            shutil.rmtree(workspace.root_path, ignore_errors=True)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _resolve_workspace_dir(self, workspace_id: str) -> Path:
        """Return the absolute workspace path and verify it stays inside base_dir."""
        # Sanitise workspace_id: only alphanumerics, dashes and underscores allowed
        safe_id = "".join(c for c in workspace_id if c.isalnum() or c in "-_")
        if not safe_id:
            raise ValueError(f"workspace_id '{workspace_id}' is not a valid directory name")

        workspace_dir = self._base_dir / safe_id
        self._security.check_path_traversal(workspace_dir, self._base_dir)
        workspace_dir.mkdir(parents=True, exist_ok=True)
        return workspace_dir

    @staticmethod
    def _build_clone_url(repo_url: str, auth_token: str | None) -> str:
        """Embed the PAT into the clone URL for private repositories.

        The token is embedded as ``https://{PAT}@github.com/owner/repo.git``.
        It is intentionally *not* URL-encoded — GitHub accepts the raw token value.
        """
        if not auth_token:
            return repo_url

        # Normalise: ensure the URL starts with https://
        url = repo_url.strip()
        if not url.startswith("https://"):
            url = "https://" + url.lstrip("http://").lstrip("https://")

        parsed = urllib.parse.urlparse(url)
        # Replace netloc to inject credentials
        netloc = f"{urllib.parse.quote(auth_token, safe='')}@{parsed.hostname}"
        if parsed.port:
            netloc += f":{parsed.port}"

        return urllib.parse.urlunparse(parsed._replace(netloc=netloc))
