"""Unit tests for maintenance checking functionality."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, Mock, patch

import pytest

from it_depends.maintenance import GitHubClient, extract_github_repo
from it_depends.models import MaintenanceInfo, Package


class TestGitHubURLExtraction:
    """Tests for extracting owner/repo from GitHub URLs."""

    def test_https_url(self) -> None:
        """Test extraction from HTTPS URL."""
        url = "https://github.com/owner/repo"
        result = extract_github_repo(url)
        assert result == ("owner", "repo")

    def test_https_url_with_git_suffix(self) -> None:
        """Test extraction from HTTPS URL with .git suffix."""
        url = "https://github.com/owner/repo.git"
        result = extract_github_repo(url)
        assert result == ("owner", "repo")

    def test_ssh_url(self) -> None:
        """Test extraction from SSH URL."""
        url = "git@github.com:owner/repo.git"
        result = extract_github_repo(url)
        assert result == ("owner", "repo")

    def test_ssh_url_without_git_suffix(self) -> None:
        """Test extraction from SSH URL without .git."""
        url = "git@github.com:owner/repo"
        result = extract_github_repo(url)
        assert result == ("owner", "repo")

    def test_url_with_subpath(self) -> None:
        """Test extraction handles URLs with subpaths."""
        url = "https://github.com/owner/repo/tree/main"
        result = extract_github_repo(url)
        assert result == ("owner", "repo")

    def test_non_github_url(self) -> None:
        """Test non-GitHub URLs return None."""
        url = "https://bitbucket.org/owner/repo"
        result = extract_github_repo(url)
        assert result is None

    def test_empty_url(self) -> None:
        """Test empty URL returns None."""
        result = extract_github_repo("")
        assert result is None

    def test_none_url(self) -> None:
        """Test None URL returns None."""
        result = extract_github_repo(None)  # type: ignore[arg-type]
        assert result is None

    def test_malformed_url(self) -> None:
        """Test malformed URL returns None."""
        url = "github.com"
        result = extract_github_repo(url)
        assert result is None


class TestMaintenanceInfo:
    """Tests for MaintenanceInfo data class."""

    def test_initialization_with_all_fields(self) -> None:
        """Test MaintenanceInfo initialization with all fields."""
        info = MaintenanceInfo(
            repository_url="https://github.com/owner/repo",
            last_commit_date="2023-05-22T14:30:00Z",
            is_stale=True,
            days_since_update=500,
            error=None,
        )
        assert info.repository_url == "https://github.com/owner/repo"
        assert info.last_commit_date == "2023-05-22T14:30:00Z"
        assert info.is_stale is True
        assert info.days_since_update == 500
        assert info.error is None

    def test_initialization_with_error(self) -> None:
        """Test MaintenanceInfo initialization with error."""
        info = MaintenanceInfo(error="Failed to fetch repository metadata")
        assert info.repository_url is None
        assert info.last_commit_date is None
        assert info.is_stale is False
        assert info.days_since_update is None
        assert info.error == "Failed to fetch repository metadata"

    def test_to_obj_serialization(self) -> None:
        """Test serialization to dictionary."""
        info = MaintenanceInfo(
            repository_url="https://github.com/owner/repo",
            last_commit_date="2023-05-22T14:30:00Z",
            is_stale=False,
            days_since_update=120,
        )
        obj = info.to_obj()
        assert obj == {
            "repository_url": "https://github.com/owner/repo",
            "last_commit_date": "2023-05-22T14:30:00Z",
            "is_stale": False,
            "days_since_update": 120,
            "error": None,
        }

    def test_equality(self) -> None:
        """Test equality comparison."""
        info1 = MaintenanceInfo(
            repository_url="https://github.com/owner/repo",
            last_commit_date="2023-05-22T14:30:00Z",
            is_stale=True,
        )
        info2 = MaintenanceInfo(
            repository_url="https://github.com/owner/repo",
            last_commit_date="2023-05-22T14:30:00Z",
            is_stale=True,
        )
        assert info1 == info2

    def test_inequality(self) -> None:
        """Test inequality comparison."""
        info1 = MaintenanceInfo(
            repository_url="https://github.com/owner/repo1",
            last_commit_date="2023-05-22T14:30:00Z",
        )
        info2 = MaintenanceInfo(
            repository_url="https://github.com/owner/repo2",
            last_commit_date="2023-05-22T14:30:00Z",
        )
        assert info1 != info2

    def test_hash(self) -> None:
        """Test hashing for set/dict usage."""
        info1 = MaintenanceInfo(
            repository_url="https://github.com/owner/repo",
            last_commit_date="2023-05-22T14:30:00Z",
            is_stale=True,
        )
        info2 = MaintenanceInfo(
            repository_url="https://github.com/owner/repo",
            last_commit_date="2023-05-22T14:30:00Z",
            is_stale=True,
        )
        # Same info should have same hash
        assert hash(info1) == hash(info2)
        # Should be usable in sets
        info_set = {info1, info2}
        assert len(info_set) == 1


class TestGitHubClient:
    """Tests for GitHubClient API interactions."""

    def test_initialization_without_token(self) -> None:
        """Test client initialization without token."""
        client = GitHubClient()
        assert "Authorization" not in client.session.headers
        assert "Accept" in client.session.headers

    def test_initialization_with_token(self) -> None:
        """Test client initialization with token."""
        client = GitHubClient(token="test_token")
        assert client.session.headers["Authorization"] == "token test_token"

    @patch("it_depends.maintenance.Session")
    def test_fetch_repo_metadata_success(self, mock_session_class: Mock) -> None:
        """Test successful repository metadata fetch."""
        # Setup mock
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.headers = {
            "X-RateLimit-Remaining": "100",
            "X-RateLimit-Reset": "1234567890",
        }
        mock_response.json.return_value = {
            "name": "repo",
            "pushed_at": "2023-05-22T14:30:00Z",
            "stargazers_count": 1000,
        }

        mock_session = Mock()
        mock_session.get.return_value = mock_response
        mock_session_class.return_value = mock_session

        client = GitHubClient()
        client.session = mock_session
        metadata = client.fetch_repo_metadata("owner", "repo")

        assert metadata is not None
        assert metadata["name"] == "repo"
        assert metadata["pushed_at"] == "2023-05-22T14:30:00Z"
        assert client.remaining_requests == 100

    @patch("it_depends.maintenance.Session")
    def test_fetch_repo_metadata_not_found(self, mock_session_class: Mock) -> None:
        """Test 404 response for non-existent repository."""
        mock_response = Mock()
        mock_response.status_code = 404

        mock_session = Mock()
        mock_session.get.return_value = mock_response
        mock_session_class.return_value = mock_session

        client = GitHubClient()
        client.session = mock_session
        metadata = client.fetch_repo_metadata("owner", "nonexistent")

        assert metadata is None

    @patch("it_depends.maintenance.Session")
    def test_fetch_repo_metadata_rate_limit(self, mock_session_class: Mock) -> None:
        """Test 403 response for rate limit exceeded."""
        mock_response = Mock()
        mock_response.status_code = 403

        mock_session = Mock()
        mock_session.get.return_value = mock_response
        mock_session_class.return_value = mock_session

        client = GitHubClient()
        client.session = mock_session
        metadata = client.fetch_repo_metadata("owner", "repo")

        assert metadata is None

    def test_extract_maintenance_date_with_pushed_at(self) -> None:
        """Test extracting maintenance date from pushed_at field."""
        client = GitHubClient()
        metadata = {
            "pushed_at": "2023-05-22T14:30:00Z",
            "created_at": "2020-01-01T00:00:00Z",
        }
        date = client.extract_maintenance_date(metadata)
        assert date == "2023-05-22T14:30:00Z"

    def test_extract_maintenance_date_fallback_to_created_at(self) -> None:
        """Test fallback to created_at when pushed_at is missing."""
        client = GitHubClient()
        metadata = {"created_at": "2020-01-01T00:00:00Z"}
        date = client.extract_maintenance_date(metadata)
        assert date == "2020-01-01T00:00:00Z"

    def test_extract_maintenance_date_no_dates(self) -> None:
        """Test returns None when no dates available."""
        client = GitHubClient()
        metadata = {"name": "repo"}
        date = client.extract_maintenance_date(metadata)
        assert date is None


class TestPackageIntegration:
    """Tests for Package class integration with maintenance info."""

    def test_package_with_maintenance_info(self) -> None:
        """Test creating package with maintenance info."""
        info = MaintenanceInfo(
            repository_url="https://github.com/owner/repo",
            last_commit_date="2023-05-22T14:30:00Z",
            is_stale=False,
            days_since_update=120,
        )
        pkg = Package(
            name="test-package",
            version="1.0.0",
            source="npm",
            maintenance_info=info,
        )
        assert pkg.maintenance_info == info

    def test_package_without_maintenance_info(self) -> None:
        """Test creating package without maintenance info."""
        pkg = Package(name="test-package", version="1.0.0", source="npm")
        assert pkg.maintenance_info is None

    def test_update_maintenance_info(self) -> None:
        """Test updating package with maintenance info."""
        pkg = Package(name="test-package", version="1.0.0", source="npm")
        info = MaintenanceInfo(
            repository_url="https://github.com/owner/repo",
            is_stale=True,
        )
        result = pkg.update_maintenance_info(info)
        assert pkg.maintenance_info == info
        assert result == pkg  # Should return self for chaining

    def test_to_obj_includes_maintenance(self) -> None:
        """Test package serialization includes maintenance info."""
        info = MaintenanceInfo(
            repository_url="https://github.com/owner/repo",
            last_commit_date="2023-05-22T14:30:00Z",
            is_stale=True,
            days_since_update=500,
        )
        pkg = Package(
            name="test-package",
            version="1.0.0",
            source="npm",
            maintenance_info=info,
        )
        obj = pkg.to_obj()
        assert "maintenance" in obj
        assert obj["maintenance"]["repository_url"] == "https://github.com/owner/repo"
        assert obj["maintenance"]["is_stale"] is True

    def test_to_obj_without_maintenance(self) -> None:
        """Test package serialization without maintenance info."""
        pkg = Package(name="test-package", version="1.0.0", source="npm")
        obj = pkg.to_obj()
        assert "maintenance" not in obj


class TestResolverURLExtraction:
    """Tests for resolver get_repository_url methods."""

    @patch("it_depends.npm.subprocess.check_output")
    def test_npm_get_repository_url(self, mock_subprocess: Mock) -> None:
        """Test NPM resolver repository URL extraction."""
        from it_depends.npm import NPMResolver

        mock_subprocess.return_value = b'{"url": "https://github.com/lodash/lodash"}'

        pkg = Package(name="lodash", version="4.17.21", source="npm")
        url = NPMResolver.get_repository_url(pkg)

        assert url == "https://github.com/lodash/lodash"

    @patch("it_depends.pip.requests.get")
    def test_pip_get_repository_url(self, mock_get: Mock) -> None:
        """Test Pip resolver repository URL extraction."""
        from it_depends.pip import PipResolver

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "info": {
                "project_urls": {
                    "Source": "https://github.com/psf/requests",
                }
            }
        }
        mock_get.return_value = mock_response

        pkg = Package(name="requests", version="2.31.0", source="pip")
        url = PipResolver.get_repository_url(pkg)

        assert url == "https://github.com/psf/requests"

    @patch("it_depends.cargo.requests.get")
    def test_cargo_get_repository_url(self, mock_get: Mock) -> None:
        """Test Cargo resolver repository URL extraction."""
        from it_depends.cargo import CargoResolver

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "crate": {"repository": "https://github.com/rust-lang/cargo"}
        }
        mock_get.return_value = mock_response

        pkg = Package(name="cargo", version="0.1.0", source="cargo")
        url = CargoResolver.get_repository_url(pkg)

        assert url == "https://github.com/rust-lang/cargo"

    def test_go_get_repository_url(self) -> None:
        """Test Go resolver repository URL extraction."""
        from it_depends.go import GoResolver

        # Test with GitHub package
        pkg = Package(name="github.com/user/repo", version="1.0.0", source="go")
        url = GoResolver.get_repository_url(pkg)
        assert url == "https://github.com/user/repo"

        # Test with non-GitHub package
        pkg2 = Package(name="golang.org/x/tools", version="1.0.0", source="go")
        url2 = GoResolver.get_repository_url(pkg2)
        assert url2 is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
