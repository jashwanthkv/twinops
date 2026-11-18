"""Async GitHub API client for repository validation and metadata retrieval."""

from __future__ import annotations

import re
from typing import Any

import httpx

from aie.models import RepoAccessResult

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

GITHUB_API_BASE = "https://api.github.com"
GITHUB_URL_RE = re.compile(
    r"^(?:https?://)?github\.com/(?P<owner>[A-Za-z0-9_.-]+)/(?P<repo>[A-Za-z0-9_.-]+?)(?:\.git)?/?$"
)
DEFAULT_TIMEOUT = 15.0  # seconds


# ---------------------------------------------------------------------------
# GitHubClient
# ---------------------------------------------------------------------------


class GitHubClient:
    """Async client for GitHub REST API v3 operations needed by the ingestion pipeline."""

    def __init__(self, timeout: float = DEFAULT_TIMEOUT) -> None:
        self._timeout = timeout

    # ------------------------------------------------------------------
    # URL parsing
    # ------------------------------------------------------------------

    @staticmethod
    def parse_github_url(url: str) -> tuple[str, str] | None:
        """Parse a GitHub repository URL and return ``(owner, repo_name)``.

        Returns *None* if the URL is not a recognisable GitHub repository URL.

        Supported formats:
        - ``https://github.com/owner/repo``
        - ``https://github.com/owner/repo.git``
        - ``github.com/owner/repo``
        """
        if not url:
            return None
        m = GITHUB_URL_RE.match(url.strip())
        if m is None:
            return None
        return m.group("owner"), m.group("repo")

    # ------------------------------------------------------------------
    # Repository access validation
    # ------------------------------------------------------------------

    async def validate_repo_access(
        self,
        repo_url: str,
        auth_token: str | None = None,
    ) -> RepoAccessResult:
        """Check whether the repository at *repo_url* is accessible.

        Returns a :class:`~aie.models.RepoAccessResult` describing the outcome.
        """
        parsed = self.parse_github_url(repo_url)
        if parsed is None:
            return RepoAccessResult(
                accessible=False,
                is_private=False,
                error="Invalid GitHub repository URL",
                owner=None,
                repo_name=None,
                default_branch=None,
            )

        owner, repo_name = parsed
        headers = self._build_headers(auth_token)

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                meta = await self.get_repo_metadata(owner, repo_name, headers, client=client)
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if status in (401, 403):
                return RepoAccessResult(
                    accessible=False,
                    is_private=True,
                    error="Authentication required or insufficient permissions",
                    owner=owner,
                    repo_name=repo_name,
                    default_branch=None,
                )
            if status == 404:
                return RepoAccessResult(
                    accessible=False,
                    is_private=False,
                    error="Repository not found",
                    owner=owner,
                    repo_name=repo_name,
                    default_branch=None,
                )
            return RepoAccessResult(
                accessible=False,
                is_private=False,
                error=f"GitHub API returned HTTP {status}",
                owner=owner,
                repo_name=repo_name,
                default_branch=None,
            )
        except httpx.RequestError as exc:
            return RepoAccessResult(
                accessible=False,
                is_private=False,
                error=f"Network error: {exc}",
                owner=owner,
                repo_name=repo_name,
                default_branch=None,
            )

        is_private: bool = meta.get("private", False)
        default_branch: str | None = meta.get("default_branch")

        return RepoAccessResult(
            accessible=True,
            is_private=is_private,
            error=None,
            owner=owner,
            repo_name=repo_name,
            default_branch=default_branch,
        )

    # ------------------------------------------------------------------
    # Metadata / branch helpers
    # ------------------------------------------------------------------

    async def get_repo_metadata(
        self,
        owner: str,
        repo: str,
        headers: dict[str, str],
        *,
        client: httpx.AsyncClient | None = None,
    ) -> dict[str, Any]:
        """Return the full GitHub repository metadata dict for ``owner/repo``.

        Raises :class:`httpx.HTTPStatusError` on non-2xx responses.
        """
        url = f"{GITHUB_API_BASE}/repos/{owner}/{repo}"
        if client is not None:
            response = await client.get(url, headers=headers)
        else:
            async with httpx.AsyncClient(timeout=self._timeout) as _client:
                response = await _client.get(url, headers=headers)

        response.raise_for_status()
        return response.json()

    async def get_default_branch(
        self,
        owner: str,
        repo: str,
        headers: dict[str, str],
    ) -> str:
        """Return the name of the default branch for ``owner/repo``.

        Falls back to ``"main"`` if the metadata does not include the field.
        """
        meta = await self.get_repo_metadata(owner, repo, headers)
        return meta.get("default_branch") or "main"

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_headers(auth_token: str | None) -> dict[str, str]:
        headers: dict[str, str] = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if auth_token:
            headers["Authorization"] = f"Bearer {auth_token}"
        return headers
