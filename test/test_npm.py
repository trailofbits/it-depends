"""Unit tests for NPM package dependency resolution."""

import json
import subprocess
from pathlib import Path
from unittest import TestCase
from unittest.mock import MagicMock, Mock, mock_open, patch

import pytest
from semantic_version import NpmSpec, SimpleSpec

from it_depends.dependencies import AliasedDependency, Dependency, DockerSetup, SourceRepository
from it_depends.npm import (
    NPMResolver,
    _get_dependencies_from_package_json,
    detect_lockfile_version,
    extract_dependencies_from_lock_v1,
    extract_dependencies_from_lock_v2_v3,
    generate_dependency_from_information,
    parse_package_lock,
)


class TestHelperFunctions(TestCase):
    """Tests for helper functions in npm.py."""

    def test_get_dependencies_from_package_json(self) -> None:
        """Test extracting dependencies from package.json."""
        package_json = {"dependencies": {"lodash": "^4.17.0", "express": "~4.18.0"}}
        mock_path = Mock(spec=Path)
        mock_path.exists.return_value = True
        mock_path.open = mock_open(read_data=json.dumps(package_json))

        result = _get_dependencies_from_package_json(mock_path)

        assert result == {"lodash": "^4.17.0", "express": "~4.18.0"}

    def test_get_dependencies_from_package_json_no_file(self) -> None:
        """Test returns empty dict when package.json doesn't exist."""
        mock_path = Mock(spec=Path)
        mock_path.exists.return_value = False

        result = _get_dependencies_from_package_json(mock_path)

        assert result == {}

    def test_get_dependencies_from_package_json_no_dependencies(self) -> None:
        """Test returns empty dict when no dependencies key exists."""
        package_json = {"name": "test-package", "version": "1.0.0"}
        mock_path = Mock(spec=Path)
        mock_path.exists.return_value = True
        mock_path.open = mock_open(read_data=json.dumps(package_json))

        result = _get_dependencies_from_package_json(mock_path)

        assert result == {}

    def test_parse_package_lock_success(self) -> None:
        """Test successful parsing of package-lock.json."""
        lock_data = {"name": "test", "lockfileVersion": 2, "packages": {}}
        mock_path = Mock(spec=Path)
        mock_path.open = mock_open(read_data=json.dumps(lock_data))

        result = parse_package_lock(mock_path)

        assert result == lock_data

    def test_parse_package_lock_file_not_found(self) -> None:
        """Test returns None when file not found."""
        mock_path = Mock(spec=Path)
        mock_path.open.side_effect = FileNotFoundError()

        result = parse_package_lock(mock_path)

        assert result is None

    def test_parse_package_lock_invalid_json(self) -> None:
        """Test returns None for invalid JSON."""
        mock_path = Mock(spec=Path)
        mock_path.open = mock_open(read_data="invalid json {{{")

        result = parse_package_lock(mock_path)

        assert result is None

    def test_detect_lockfile_version_v1(self) -> None:
        """Test detection of lockfile version 1."""
        lock_data = {"lockfileVersion": 1}

        result = detect_lockfile_version(lock_data)

        assert result == 1

    def test_detect_lockfile_version_v2(self) -> None:
        """Test detection of lockfile version 2."""
        lock_data = {"lockfileVersion": 2}

        result = detect_lockfile_version(lock_data)

        assert result == 2  # noqa: PLR2004

    def test_detect_lockfile_version_v3(self) -> None:
        """Test detection of lockfile version 3."""
        lock_data = {"lockfileVersion": 3}

        result = detect_lockfile_version(lock_data)

        assert result == 3  # noqa: PLR2004

    def test_detect_lockfile_version_default(self) -> None:
        """Test defaults to version 1 when key missing."""
        lock_data: dict[str, int] = {}

        result = detect_lockfile_version(lock_data)

        assert result == 1

    def test_extract_dependencies_from_lock_v2_v3(self) -> None:
        """Test extracting dependencies from v2/v3 lock file."""
        lock_data = {
            "packages": {
                "": {"dependencies": {"lodash": "^4.17.0", "express": "^4.18.0"}},
                "node_modules/lodash": {"version": "4.17.21"},
            }
        }

        result = extract_dependencies_from_lock_v2_v3(lock_data)

        assert result == {"lodash": "^4.17.0", "express": "^4.18.0"}

    def test_extract_dependencies_from_lock_v2_v3_empty(self) -> None:
        """Test handles missing packages or dependencies keys."""
        assert extract_dependencies_from_lock_v2_v3({}) == {}
        assert extract_dependencies_from_lock_v2_v3({"packages": {}}) == {}
        assert extract_dependencies_from_lock_v2_v3({"packages": {"": {}}}) == {}

    def test_extract_dependencies_from_lock_v1(self) -> None:
        """Test v1 extraction returns empty dict (requires package.json fallback)."""
        lock_data = {"dependencies": {"lodash": {"version": "4.17.21"}}}

        result = extract_dependencies_from_lock_v1(lock_data)

        assert result == {}

    def test_generate_dependency_from_information_normal(self) -> None:
        """Test generating a normal Dependency."""
        result = generate_dependency_from_information("lodash", "^4.17.0", "npm")

        assert isinstance(result, Dependency)
        assert result.package == "lodash"
        assert result.source == "npm"
        assert isinstance(result.semantic_version, (NpmSpec, SimpleSpec))

    def test_generate_dependency_from_information_aliased(self) -> None:
        """Test generating an AliasedDependency with npm: prefix."""
        result = generate_dependency_from_information("my-lodash", "npm:@scope/lodash@^4.17.0", "npm")

        assert isinstance(result, AliasedDependency)
        assert result.package == "my-lodash"
        assert result.alias_name == "scope/lodash"
        assert result.source == "npm"

    def test_generate_dependency_from_information_unsupported_alias(self) -> None:
        """Test raises ValueError for unsupported npm: alias format."""
        with pytest.raises(ValueError, match="not yet supported"):
            generate_dependency_from_information("alias", "npm:package", "npm")

    def test_generate_dependency_from_information_with_resolver(self) -> None:
        """Test accepts NPMResolver instance as source."""
        resolver = NPMResolver()

        result = generate_dependency_from_information("lodash", "^4.17.0", resolver)

        assert isinstance(result, Dependency)
        # Source is normalized to resolver name string
        assert result.source == "npm"


class TestNPMResolver(TestCase):
    """Tests for NPMResolver class."""

    def setUp(self) -> None:
        """Set up test fixtures."""
        self.resolver = NPMResolver()

    def test_name_and_description(self) -> None:
        """Test resolver has correct name and description."""
        assert self.resolver.name == "npm"
        assert "npm" in self.resolver.description.lower()

    @patch.object(NPMResolver, "is_available")
    def test_can_resolve_from_source_true(self, mock_is_available: Mock) -> None:
        """Test returns True when npm available and package.json exists."""
        mock_is_available.return_value = True
        mock_repo = Mock(spec=SourceRepository)
        mock_path = MagicMock()
        mock_path.__truediv__.return_value.exists.return_value = True
        mock_repo.path = mock_path

        result = self.resolver.can_resolve_from_source(mock_repo)

        assert result is True

    @patch.object(NPMResolver, "is_available")
    def test_can_resolve_from_source_false_no_npm(self, mock_is_available: Mock) -> None:
        """Test returns False when npm not available."""
        mock_is_available.return_value = False
        mock_repo = Mock(spec=SourceRepository)

        result = self.resolver.can_resolve_from_source(mock_repo)

        assert result is False

    @patch.object(NPMResolver, "is_available")
    def test_can_resolve_from_source_false_no_package_json(self, mock_is_available: Mock) -> None:
        """Test returns False when package.json doesn't exist."""
        mock_is_available.return_value = True
        mock_repo = Mock(spec=SourceRepository)
        mock_path = MagicMock()
        mock_path.__truediv__.return_value.exists.return_value = False
        mock_repo.path = mock_path

        result = self.resolver.can_resolve_from_source(mock_repo)

        assert result is False

    @patch.object(NPMResolver, "can_resolve_from_source")
    @patch.object(NPMResolver, "from_package_json")
    def test_resolve_from_source_success(self, mock_from_package_json: Mock, mock_can_resolve: Mock) -> None:
        """Test successful resolution from source."""
        mock_can_resolve.return_value = True
        mock_source_pkg = Mock()
        mock_from_package_json.return_value = mock_source_pkg
        mock_repo = Mock(spec=SourceRepository)

        result = self.resolver.resolve_from_source(mock_repo)

        assert result == mock_source_pkg
        mock_from_package_json.assert_called_once_with(mock_repo)

    @patch.object(NPMResolver, "can_resolve_from_source")
    def test_resolve_from_source_returns_none(self, mock_can_resolve: Mock) -> None:
        """Test returns None when can_resolve returns False."""
        mock_can_resolve.return_value = False
        mock_repo = Mock(spec=SourceRepository)

        result = self.resolver.resolve_from_source(mock_repo)

        assert result is None

    @patch("it_depends.npm.parse_package_lock")
    @patch("it_depends.npm.detect_lockfile_version")
    @patch("it_depends.npm.extract_dependencies_from_lock_v2_v3")
    def test_from_package_json_with_lockfile_v2(
        self,
        mock_extract: Mock,
        mock_detect: Mock,
        mock_parse: Mock,
    ) -> None:
        """Test parsing with lockfile v2/v3."""
        mock_parse.return_value = {"name": "test-pkg", "version": "1.0.0"}
        mock_detect.return_value = 2
        mock_extract.return_value = {"lodash": "^4.17.0"}

        mock_path = MagicMock()
        mock_lock_path = MagicMock()
        mock_pkg_path = MagicMock()
        mock_lock_path.exists.return_value = True
        mock_pkg_path.exists.return_value = True
        mock_path.__truediv__ = lambda _, key: mock_lock_path if "lock" in key else mock_pkg_path

        mock_repo = Mock(spec=SourceRepository)
        mock_repo.path = mock_path

        result = NPMResolver.from_package_json(mock_repo)

        assert result.name == "test-pkg"
        assert result.source == "npm"
        mock_extract.assert_called_once()

    @patch("it_depends.npm.parse_package_lock")
    @patch("it_depends.npm.detect_lockfile_version")
    @patch("it_depends.npm._get_dependencies_from_package_json")
    def test_from_package_json_with_lockfile_v1(
        self,
        mock_get_deps: Mock,
        mock_detect: Mock,
        mock_parse: Mock,
    ) -> None:
        """Test lockfile v1 falls back to package.json for deps."""
        mock_parse.return_value = {"name": "test-pkg", "version": "1.0.0"}
        mock_detect.return_value = 1
        mock_get_deps.return_value = {"lodash": "^4.17.0"}

        mock_path = MagicMock()
        mock_lock_path = MagicMock()
        mock_pkg_path = MagicMock()
        mock_lock_path.exists.return_value = True
        mock_path.__truediv__ = lambda _, key: mock_lock_path if "lock" in key else mock_pkg_path

        mock_repo = Mock(spec=SourceRepository)
        mock_repo.path = mock_path

        result = NPMResolver.from_package_json(mock_repo)

        assert result.name == "test-pkg"
        mock_get_deps.assert_called_once()

    def test_from_package_json_no_lockfile(self) -> None:
        """Test parsing with only package.json (no lockfile)."""
        package_json = {"name": "test-pkg", "version": "2.0.0", "dependencies": {"express": "^4.18.0"}}

        mock_path = MagicMock()
        mock_lock_path = MagicMock()
        mock_pkg_path = MagicMock()
        mock_lock_path.exists.return_value = False
        mock_pkg_path.exists.return_value = True
        mock_pkg_path.open = mock_open(read_data=json.dumps(package_json))
        mock_path.__truediv__ = lambda _, key: mock_lock_path if "lock" in key else mock_pkg_path
        mock_path.parent.name = "fallback-name"

        mock_repo = Mock(spec=SourceRepository)
        mock_repo.path = mock_path

        result = NPMResolver.from_package_json(mock_repo)

        assert result.name == "test-pkg"
        assert str(result.version) == "2.0.0"

    def test_from_package_json_no_files(self) -> None:
        """Test raises ValueError when no package files exist."""
        mock_path = MagicMock()
        mock_lock_path = MagicMock()
        mock_pkg_path = MagicMock()
        mock_lock_path.exists.return_value = False
        mock_pkg_path.exists.return_value = False
        mock_path.__truediv__ = lambda _, key: mock_lock_path if "lock" in key else mock_pkg_path

        mock_repo = Mock(spec=SourceRepository)
        mock_repo.path = mock_path

        with pytest.raises(ValueError, match=r"package-lock\.json or package\.json"):
            NPMResolver.from_package_json(mock_repo)

    @patch("it_depends.npm.parse_package_lock")
    @patch("it_depends.npm.detect_lockfile_version")
    @patch("it_depends.npm.extract_dependencies_from_lock_v2_v3")
    def test_from_package_json_with_source_repository(
        self,
        mock_extract: Mock,
        mock_detect: Mock,
        mock_parse: Mock,
    ) -> None:
        """Test accepts SourceRepository as input."""
        mock_parse.return_value = {"name": "repo-pkg", "version": "1.0.0"}
        mock_detect.return_value = 3
        mock_extract.return_value = {}

        mock_path = MagicMock(spec=Path)
        mock_lock_path = MagicMock()
        mock_pkg_path = MagicMock()
        mock_lock_path.exists.return_value = True
        mock_path.__truediv__ = lambda _, key: mock_lock_path if "lock" in key else mock_pkg_path

        mock_repo = Mock(spec=SourceRepository)
        mock_repo.path = mock_path

        result = NPMResolver.from_package_json(mock_repo)

        assert result.name == "repo-pkg"
        assert result.source_repo == mock_repo

    @patch("it_depends.npm.subprocess.check_output")
    def test_resolve_single_version(self, mock_subprocess: Mock) -> None:
        """Test resolving when npm view returns single package."""
        npm_output = {"name": "lodash", "version": "4.17.21", "dependencies": {"dep1": "^1.0.0"}}
        mock_subprocess.return_value = json.dumps(npm_output).encode()

        dep = Dependency(package="lodash", semantic_version=SimpleSpec("^4.17.0"), source="npm")
        packages = list(self.resolver.resolve(dep))

        assert len(packages) == 1
        assert packages[0].name == "lodash"
        assert str(packages[0].version) == "4.17.21"

    @patch("it_depends.npm.subprocess.check_output")
    def test_resolve_multiple_versions(self, mock_subprocess: Mock) -> None:
        """Test resolving when npm view returns multiple packages."""
        npm_output = [
            {"name": "lodash", "version": "4.17.21", "dependencies": {}},
            {"name": "lodash", "version": "4.17.20", "dependencies": {}},
        ]
        mock_subprocess.return_value = json.dumps(npm_output).encode()

        dep = Dependency(package="lodash", semantic_version=SimpleSpec("^4.17.0"), source="npm")
        packages = list(self.resolver.resolve(dep))

        assert len(packages) == 2  # noqa: PLR2004
        versions = {str(p.version) for p in packages}
        assert versions == {"4.17.21", "4.17.20"}

    def test_resolve_wrong_source(self) -> None:
        """Test skips dependencies from other sources."""
        dep = Dependency(package="requests", semantic_version=SimpleSpec("*"), source="pip")

        packages = list(self.resolver.resolve(dep))

        assert packages == []

    @patch("it_depends.npm.subprocess.check_output")
    def test_resolve_scoped_package(self, mock_subprocess: Mock) -> None:
        """Test resolving scoped package (@scope/name)."""
        npm_output = {"name": "@babel/core", "version": "7.20.0", "dependencies": {}}
        mock_subprocess.return_value = json.dumps(npm_output).encode()

        dep = Dependency(package="babel/core", semantic_version=SimpleSpec("^7.0.0"), source="npm")
        list(self.resolver.resolve(dep))

        call_args = mock_subprocess.call_args[0][0]
        assert "@babel/core@" in " ".join(call_args)

    @patch("it_depends.npm.subprocess.check_output")
    def test_resolve_aliased_dependency(self, mock_subprocess: Mock) -> None:
        """Test resolving AliasedDependency."""
        npm_output = {"name": "my-lodash", "version": "4.17.21", "dependencies": {}}
        mock_subprocess.return_value = json.dumps(npm_output).encode()

        dep = AliasedDependency(
            package="my-lodash",
            alias_name="lodash",
            semantic_version=SimpleSpec("^4.17.0"),
            source="npm",
        )
        list(self.resolver.resolve(dep))

        call_args = mock_subprocess.call_args[0][0]
        assert "@lodash@" in " ".join(call_args)

    @patch("it_depends.npm.subprocess.check_output")
    def test_resolve_subprocess_error(self, mock_subprocess: Mock) -> None:
        """Test handles CalledProcessError gracefully."""
        mock_subprocess.side_effect = subprocess.CalledProcessError(1, "npm")

        dep = Dependency(package="nonexistent", semantic_version=SimpleSpec("*"), source="npm")
        packages = list(self.resolver.resolve(dep))

        assert packages == []

    @patch("it_depends.npm.subprocess.check_output")
    def test_resolve_json_parse_error(self, mock_subprocess: Mock) -> None:
        """Test raises ValueError for invalid JSON output."""
        mock_subprocess.return_value = b"invalid json"

        dep = Dependency(package="lodash", semantic_version=SimpleSpec("*"), source="npm")

        with pytest.raises(ValueError, match="Error parsing output"):
            list(self.resolver.resolve(dep))

    def test_parse_spec_npm_spec(self) -> None:
        """Test parsing valid npm spec."""
        result = NPMResolver.parse_spec("^4.17.0")

        assert isinstance(result, NpmSpec)

    def test_parse_spec_simple_spec(self) -> None:
        """Test parsing simple spec when npm spec fails."""
        result = NPMResolver.parse_spec("4.17.21")

        assert isinstance(result, (NpmSpec, SimpleSpec))

    def test_parse_spec_with_whitespace(self) -> None:
        """Test strips whitespace and retries parsing."""
        result = NPMResolver.parse_spec("^ 4.17.0")

        assert isinstance(result, (NpmSpec, SimpleSpec))

    def test_parse_spec_invalid_returns_wildcard(self) -> None:
        """Test returns wildcard spec for completely invalid input."""
        result = NPMResolver.parse_spec("not-a-version-spec!!!")

        assert isinstance(result, SimpleSpec)
        assert str(result) == "*"

    def test_docker_setup(self) -> None:
        """Test returns correct DockerSetup configuration."""
        setup = self.resolver.docker_setup()

        assert isinstance(setup, DockerSetup)
        assert "npm" in setup.apt_get_packages
        assert "npm install" in setup.install_package_script
        assert "node" in setup.load_package_script

    @patch("it_depends.npm.parse_package_lock")
    def test_from_package_json_unsupported_lockfile_version(self, mock_parse: Mock) -> None:
        """Test raises ValueError for unsupported lockfile version."""
        mock_parse.return_value = {"name": "test", "version": "1.0.0", "lockfileVersion": 99}

        mock_path = MagicMock()
        mock_lock_path = MagicMock()
        mock_pkg_path = MagicMock()
        mock_lock_path.exists.return_value = True
        mock_path.__truediv__ = lambda _, key: mock_lock_path if "lock" in key else mock_pkg_path

        mock_repo = Mock(spec=SourceRepository)
        mock_repo.path = mock_path

        with pytest.raises(ValueError, match="Unsupported lockfileVersion"):
            NPMResolver.from_package_json(mock_repo)

    @patch("it_depends.npm.parse_package_lock")
    def test_from_package_json_failed_parse(self, mock_parse: Mock) -> None:
        """Test raises ValueError when parse_package_lock fails."""
        mock_parse.return_value = None

        mock_path = MagicMock()
        mock_lock_path = MagicMock()
        mock_pkg_path = MagicMock()
        mock_lock_path.exists.return_value = True
        mock_path.__truediv__ = lambda _, key: mock_lock_path if "lock" in key else mock_pkg_path

        mock_repo = Mock(spec=SourceRepository)
        mock_repo.path = mock_path

        with pytest.raises(ValueError, match=r"Failed to parse package-lock\.json"):
            NPMResolver.from_package_json(mock_repo)
