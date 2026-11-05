"""Best-effort checks of default-branch commit activity on GitHub."""

from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from http import HTTPStatus
from typing import TYPE_CHECKING, Any
from urllib.parse import quote, urlsplit

import requests

from it_depends.models import AliasedDependency, MaintenanceInfo, Package, SourcePackage

if TYPE_CHECKING:
    from collections.abc import Callable

    from it_depends.cache import PackageRepository
    from it_depends.models import Dependency


def extract_github_repo(url: str) -> tuple[str, str] | None:
    """Extract a validated owner and repository from HTTPS, Git, or SSH URLs."""
    url = url.removeprefix("git+")
    if url.startswith("git@github.com:"):
        url = "ssh://git@github.com/" + url.removeprefix("git@github.com:")
    try:
        parsed = urlsplit(url)
    except ValueError:
        return None
    if parsed.scheme not in {"https", "http", "git", "ssh"} or parsed.hostname != "github.com":
        return None
    parts = parsed.path.strip("/").split("/")
    if len(parts) < 2:  # noqa: PLR2004
        return None
    owner, repo = parts[0], parts[1].removesuffix(".git")
    if not re.fullmatch(r"[A-Za-z0-9-]+", owner) or not re.fullmatch(r"[A-Za-z0-9_.-]+", repo):
        return None
    return (owner, repo) if repo not in {".", ".."} else None


def _get_json(url: str, token: str | None = None) -> Any:  # noqa: ANN401
    """Fetch JSON with bounded latency; send credentials only to the GitHub API."""
    headers = {"Accept": "application/json", "User-Agent": "it-depends"}
    if token and urlsplit(url).hostname == "api.github.com":
        headers["Authorization"] = f"Bearer {token}"
    response = requests.get(url, headers=headers, timeout=10)
    response.raise_for_status()
    return response.json()


def _repository_url(package: Package, get_json: Callable[[str], Any]) -> str | None:
    """Look up registry metadata without changing the dependency resolvers."""
    name = package.name
    if package.source == "npm" and name.count("/") == 1 and not name.startswith("@"):
        name = f"@{name}"
    name = quote(name, safe="")
    if package.source == "go":
        # The shared parser validates the exact hostname before any GitHub request.
        return "https://" + package.name
    if package.source == "pip":
        info = get_json(f"https://pypi.org/pypi/{name}/json")["info"]
        urls = info.get("project_urls") or {}
        candidates = [urls.get(key) for key in ("Source", "Source Code", "Repository", "Code")]
        candidates.extend(urls.values())
        candidates.append(info.get("home_page"))
        return next((url for url in candidates if isinstance(url, str) and extract_github_repo(url)), None)
    if package.source == "npm":
        repository = get_json(f"https://registry.npmjs.org/{name}").get("repository")
        return repository.get("url") if isinstance(repository, dict) else repository
    if package.source == "cargo":
        repository = get_json(f"https://crates.io/api/v1/crates/{name}")["crate"].get("repository")
        return repository if isinstance(repository, str) else None
    return None


def _check_package(package: Package, stale_days: int, get_json: Callable[[str], Any]) -> MaintenanceInfo:
    """Check one package, preserving failures as unknown maintenance status."""
    info = MaintenanceInfo()
    try:
        if isinstance(package, SourcePackage):
            info.error = "Local source package has no verified registry repository"
            return info
        url = _repository_url(package, get_json)
        github_repo = extract_github_repo(url) if isinstance(url, str) else None
        if github_repo is None:
            info.error = "No GitHub repository found"
            return info
        owner, repo = github_repo
        info.repository_url = f"https://github.com/{owner}/{repo}"
        commits = get_json(f"https://api.github.com/repos/{owner.lower()}/{repo.lower()}/commits?per_page=1")
        date = commits[0]["commit"]["committer"]["date"]
        last_commit = datetime.fromisoformat(date.replace("Z", "+00:00"))
        if last_commit.tzinfo is None:
            info.error = "Commit date has no timezone"
            return info
        info.last_commit_date = date
        info.days_since_update = max(0, (datetime.now(timezone.utc) - last_commit).days)
        info.is_stale = info.days_since_update > stale_days
    except requests.HTTPError as error:
        status = error.response.status_code if error.response is not None else "unknown"
        if _is_rate_limited(error):
            info.error = f"API rate limit reached (HTTP {status}); retry after the limit resets"
            if info.repository_url:
                info.error += "; configure GITHUB_TOKEN for authenticated GitHub access"
        else:
            info.error = f"Maintenance lookup failed (HTTP {status}); check access and API rate limits"
    except requests.RequestException:
        info.error = "Maintenance request failed; check network access and retry"
    except (ValueError, TypeError, KeyError, IndexError, AttributeError):
        info.error = "Missing or invalid registry metadata or GitHub commit date"
    return info


def _is_rate_limited(error: requests.HTTPError) -> bool:
    """Recognize throttling without treating ordinary access failures as rate limits."""
    response = error.response
    if response is None:
        return False
    if response.status_code == HTTPStatus.TOO_MANY_REQUESTS:
        return True
    if response.status_code != HTTPStatus.FORBIDDEN:
        return False
    if response.headers.get("X-RateLimit-Remaining") == "0" or "Retry-After" in response.headers:
        return True
    # GitHub secondary limits may omit the retry/quota headers.
    try:
        data = response.json()
    except ValueError:
        return False
    message = data.get("message") if isinstance(data, dict) else None
    return isinstance(message, str) and "rate limit" in message.lower()


def _npm_dependencies(repo: PackageRepository) -> dict[str, list[Dependency]]:
    """Index npm dependency declarations by their logical package names."""
    dependencies: dict[str, list[Dependency]] = {}
    for package in repo:
        for dependency in package.dependencies:
            if dependency.source == "npm":
                dependencies.setdefault(dependency.package, []).append(dependency)
    return dependencies


def _npm_registry_package(package: Package, dependencies: list[Dependency]) -> Package | None:
    """Use a unique version-matching alias target without renaming the graph node."""
    if package.source != "npm" or isinstance(package, SourcePackage):
        return package
    if not any(isinstance(dependency, AliasedDependency) for dependency in dependencies):
        return package
    targets = set()
    for dependency in dependencies:
        if dependency.match(package):
            target = package.name
            if isinstance(dependency, AliasedDependency):
                target = f"@{dependency.alias_name.removeprefix('@')}"
            targets.add(target)
    if len(targets) != 1:
        return None
    return Package(targets.pop(), package.version, package.source)


def check_maintenance_status(
    repo: PackageRepository,
    stale_threshold_days: int = 365,
    github_token: str | None = None,
) -> PackageRepository:
    """Enrich packages sequentially with GitHub default-branch commit activity.

    Args:
        repo: Packages to enrich in place.
        stale_threshold_days: Nonnegative number of whole days allowed without a commit.
        github_token: Optional token; defaults to GITHUB_TOKEN from the environment.

    Returns:
        The supplied repository, including errors for packages that could not be checked.

    """
    if stale_threshold_days < 0:
        msg = "stale_threshold_days must be nonnegative"
        raise ValueError(msg)
    token = github_token or os.getenv("GITHUB_TOKEN")
    responses: dict[str, Any] = {}
    github_limit: requests.HTTPError | None = None

    def get_json(url: str) -> Any:  # noqa: ANN401
        """Reuse responses and failures for this run, honoring GitHub throttling."""
        nonlocal github_limit
        if url not in responses:
            is_github = urlsplit(url).hostname == "api.github.com"
            if is_github and github_limit is not None:
                raise github_limit
            try:
                responses[url] = _get_json(url, token)
            except requests.RequestException as error:
                responses[url] = error
                if is_github and isinstance(error, requests.HTTPError) and _is_rate_limited(error):
                    github_limit = error
        result = responses[url]
        if isinstance(result, requests.RequestException):
            raise result
        return result

    npm_dependencies = _npm_dependencies(repo)
    for package in repo:
        registry_package = _npm_registry_package(package, npm_dependencies.get(package.name, []))
        if registry_package is None:
            message = f"Cannot verify npm alias target for {package}"
            message += "; re-resolve with unambiguous dependency metadata"
            package.maintenance_info = MaintenanceInfo(error=message)
        else:
            package.maintenance_info = _check_package(registry_package, stale_threshold_days, get_json)
    return repo
