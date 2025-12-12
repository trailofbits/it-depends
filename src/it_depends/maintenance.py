"""Package maintenance status checking functionality."""

from __future__ import annotations

import logging
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from requests import Session
from tqdm import tqdm

if TYPE_CHECKING:
    from .cache import DBPackageCache
    from .dependencies import Package, PackageRepository
    from .models import MaintenanceInfo

logger = logging.getLogger(__name__)


def extract_github_repo(url: str) -> tuple[str, str] | None:
    """Extract owner and repo from GitHub URL.

    Args:
        url: GitHub repository URL in various formats

    Returns:
        Tuple of (owner, repo) or None if not a GitHub URL

    Examples:
        >>> extract_github_repo("https://github.com/owner/repo")
        ('owner', 'repo')
        >>> extract_github_repo("git@github.com:owner/repo.git")
        ('owner', 'repo')
        >>> extract_github_repo("https://github.com/owner/repo.git")
        ('owner', 'repo')

    """
    if not url:
        return None

    # Handle various URL formats
    patterns = [
        r"github\.com[:/]([^/]+)/([^/\.]+)(?:\.git)?",  # HTTPS or SSH
        r"github\.com/([^/]+)/([^/]+?)(?:\.git)?$",  # With optional .git suffix
    ]

    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            owner, repo = match.groups()
            # Remove .git suffix if present
            repo = repo.rstrip(".git")
            return (owner, repo)

    return None


class GitHubClient:
    """Client for GitHub API interactions."""

    API_BASE = "https://api.github.com"

    def __init__(self, token: str | None = None) -> None:
        """Initialize GitHub API client.

        Args:
            token: GitHub personal access token for authentication

        """
        self.session = Session()
        if token:
            self.session.headers["Authorization"] = f"token {token}"
        self.session.headers["Accept"] = "application/vnd.github.v3+json"
        self.remaining_requests: int | None = None
        self.reset_time: int | None = None

    def fetch_repo_metadata(self, owner: str, repo: str) -> dict | None:
        """Fetch repository metadata from GitHub API.

        Args:
            owner: Repository owner
            repo: Repository name

        Returns:
            Repository metadata dict or None if failed

        """
        try:
            response = self.session.get(
                f"{self.API_BASE}/repos/{owner}/{repo}",
                timeout=10,
            )

            # Track rate limits
            self.remaining_requests = int(response.headers.get("X-RateLimit-Remaining", 0))
            self.reset_time = int(response.headers.get("X-RateLimit-Reset", 0))

            if self.remaining_requests is not None and self.remaining_requests < 10:
                logger.warning(
                    f"GitHub API rate limit low: {self.remaining_requests} requests remaining"
                )

            if response.status_code == 404:
                logger.debug(f"Repository not found: {owner}/{repo}")
                return None

            if response.status_code == 403:
                logger.warning("GitHub API rate limit exceeded")
                return None

            response.raise_for_status()
            return response.json()

        except Exception as e:
            logger.debug(f"GitHub API error for {owner}/{repo}: {e}")
            return None

    def extract_maintenance_date(self, metadata: dict) -> str | None:
        """Extract last maintenance date from repo metadata.

        Args:
            metadata: Repository metadata from GitHub API

        Returns:
            ISO 8601 timestamp string or None

        """
        # Use pushed_at (reflects actual code commits)
        pushed_at = metadata.get("pushed_at")
        if pushed_at:
            return pushed_at

        # Fallback to created_at if no pushes
        created_at = metadata.get("created_at")
        if created_at:
            return created_at

        return None


def check_maintenance_status(
    repo: PackageRepository,
    stale_threshold_days: int = 365,
    github_token: str | None = None,
    cache: DBPackageCache | None = None,
    cache_ttl: int = 86400,
    nworkers: int | None = None,
) -> PackageRepository:
    """Enrich packages with maintenance information.

    Args:
        repo: Package repository to enrich
        stale_threshold_days: Days threshold for staleness
        github_token: GitHub API token
        cache: Database cache for GitHub metadata
        cache_ttl: Cache TTL in seconds
        nworkers: Number of worker threads

    Returns:
        Enriched package repository

    """
    from .models import MaintenanceInfo

    github_client = GitHubClient(token=github_token)

    def _check_package_maintenance(pkg: Package) -> tuple[Package, MaintenanceInfo]:
        """Check maintenance status for a single package."""
        # Try to get repository URL from package resolver
        repo_url = None
        try:
            if hasattr(pkg.resolver, "get_repository_url"):
                repo_url = pkg.resolver.get_repository_url(pkg)
        except Exception as e:
            logger.debug(f"Failed to get repo URL for {pkg.name}: {e}")

        if not repo_url:
            return (
                pkg,
                MaintenanceInfo(error="No GitHub repository URL found"),
            )

        # Extract owner/repo from URL
        github_info = extract_github_repo(repo_url)
        if not github_info:
            return (
                pkg,
                MaintenanceInfo(
                    repository_url=repo_url,
                    error="Repository not hosted on GitHub",
                ),
            )

        owner, repo_name = github_info

        # Check cache first (if available)
        if cache:
            cached = _get_cached_metadata(cache, owner, repo_name, cache_ttl, stale_threshold_days)
            if cached:
                return (pkg, cached)

        # Fetch from GitHub API
        metadata = github_client.fetch_repo_metadata(owner, repo_name)
        if not metadata:
            return (
                pkg,
                MaintenanceInfo(
                    repository_url=repo_url,
                    error="Failed to fetch repository metadata",
                ),
            )

        # Extract maintenance date
        last_commit_str = github_client.extract_maintenance_date(metadata)
        if not last_commit_str:
            return (
                pkg,
                MaintenanceInfo(
                    repository_url=repo_url,
                    error="No commit date found",
                ),
            )

        # Calculate staleness
        try:
            last_commit = datetime.fromisoformat(last_commit_str.replace("Z", "+00:00"))
            now = datetime.now(timezone.utc)
            days_since = (now - last_commit).days
            is_stale = days_since > stale_threshold_days
        except (ValueError, AttributeError) as e:
            logger.debug(f"Failed to parse date for {owner}/{repo_name}: {e}")
            return (
                pkg,
                MaintenanceInfo(
                    repository_url=repo_url,
                    error="Failed to parse commit date",
                ),
            )

        maintenance_info = MaintenanceInfo(
            repository_url=repo_url,
            last_commit_date=last_commit_str,
            is_stale=is_stale,
            days_since_update=days_since,
        )

        # Cache result
        if cache:
            _cache_metadata(cache, owner, repo_name, last_commit_str)

        return (pkg, maintenance_info)

    # Process packages in parallel
    with (
        ThreadPoolExecutor(max_workers=nworkers) as executor,
        tqdm(desc="Checking maintenance status", leave=False, unit=" packages") as t,
    ):
        futures = {executor.submit(_check_package_maintenance, pkg): pkg for pkg in repo}
        t.total = len(futures)

        for future in as_completed(futures):
            try:
                t.update(1)
                pkg, maintenance_info = future.result()
                pkg.update_maintenance_info(maintenance_info)
            except Exception:
                logger.exception("Failed to check maintenance status")

    # Log summary
    stale_count = sum(1 for pkg in repo if pkg.maintenance_info and pkg.maintenance_info.is_stale)
    if stale_count > 0:
        logger.info(
            f"Found {stale_count} stale packages (>{stale_threshold_days} days since update)"
        )

    return repo


def _get_cached_metadata(
    cache: DBPackageCache,
    owner: str,
    repo: str,
    ttl: int,
    stale_threshold_days: int,
) -> MaintenanceInfo | None:
    """Get cached GitHub metadata if still valid.

    Args:
        cache: Database cache
        owner: Repository owner
        repo: Repository name
        ttl: Cache time-to-live in seconds
        stale_threshold_days: Threshold for staleness calculation

    Returns:
        MaintenanceInfo if cache is valid, None otherwise

    """
    from .db import GitHubMetadataCache
    from .models import MaintenanceInfo

    try:
        result = (
            cache.session.query(GitHubMetadataCache)
            .filter(
                GitHubMetadataCache.owner == owner,
                GitHubMetadataCache.repo == repo,
            )
            .first()
        )

        if not result:
            return None

        # Check if cache is still valid
        fetched_at = datetime.fromisoformat(result.fetched_at)
        if (datetime.now(timezone.utc) - fetched_at).total_seconds() > ttl:
            return None

        # Reconstruct MaintenanceInfo from cache
        if result.pushed_at:
            last_commit = datetime.fromisoformat(result.pushed_at.replace("Z", "+00:00"))
            days_since = (datetime.now(timezone.utc) - last_commit).days
            return MaintenanceInfo(
                repository_url=f"https://github.com/{owner}/{repo}",
                last_commit_date=result.pushed_at,
                is_stale=days_since > stale_threshold_days,
                days_since_update=days_since,
            )

        return None
    except Exception as e:
        logger.debug(f"Cache lookup failed: {e}")
        return None


def _cache_metadata(
    cache: DBPackageCache,
    owner: str,
    repo: str,
    pushed_at: str,
) -> None:
    """Store GitHub metadata in cache.

    Args:
        cache: Database cache
        owner: Repository owner
        repo: Repository name
        pushed_at: ISO 8601 timestamp of last push

    """
    from .db import GitHubMetadataCache

    try:
        cache.session.merge(
            GitHubMetadataCache(
                owner=owner,
                repo=repo,
                pushed_at=pushed_at,
                fetched_at=datetime.now(timezone.utc).isoformat(),
            )
        )
        cache.session.commit()
    except Exception as e:
        logger.debug(f"Failed to cache metadata: {e}")
