"""
aie/utils.py — Helper utilities for TwinOps AIE.

Includes URL parsing, project/repo regex extraction, and name sanitization.
"""

from __future__ import annotations

import re


def extract_project_from_url(repo_url: str) -> tuple[str, str]:
    """Extract (owner_id, project_name) from a GitHub repository URL using regex.

    Examples:
        https://github.com/pallets/flask -> ('pallets', 'flask')
        https://github.com/pallets/flask.git -> ('pallets', 'flask')
        git@github.com:my-org/core-service.git -> ('my-org', 'core-service')
    """
    if not repo_url:
        return "default", "aie_project"

    clean_url = repo_url.strip()

    # Regex matches: github.com/<owner>/<project> (with optional .git or trailing slash)
    match = re.search(
        r"github\.com[/:](?P<owner>[^/]+)/(?P<project>[^/.]+?)(?:\.git|/)?$",
        clean_url,
    )
    if match:
        owner = match.group("owner")
        project = match.group("project")
        # Sanitize for DB and Redis key safety
        safe_project = re.sub(r"[^a-zA-Z0-9_-]", "_", project).strip("_")
        safe_owner = re.sub(r"[^a-zA-Z0-9_-]", "_", owner).strip("_")
        return safe_owner or "default", safe_project or "aie_project"

    # Fallback for generic git URLs
    clean = clean_url.rstrip("/")
    if clean.endswith(".git"):
        clean = clean[:-4]
    parts = clean.split("/")
    if len(parts) >= 2:
        safe_owner = re.sub(r"[^a-zA-Z0-9_-]", "_", parts[-2]).strip("_")
        safe_project = re.sub(r"[^a-zA-Z0-9_-]", "_", parts[-1]).strip("_")
        return safe_owner or "default", safe_project or "aie_project"
    elif len(parts) == 1:
        safe_proj = re.sub(r"[^a-zA-Z0-9_-]", "_", parts[0]).strip("_")
        return "default", safe_proj or "aie_project"

    return "default", "aie_project"
