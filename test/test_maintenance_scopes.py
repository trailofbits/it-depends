"""Maintenance lookup of npm packages with scoped names."""

from __future__ import annotations

import json
from unittest.mock import Mock, patch

import pytest

from it_depends.cache import PackageRepository
from it_depends.maintenance import check_maintenance_status
from it_depends.models import Dependency, Package
from it_depends.npm import NPMResolver


def test_scoped_cli_package_maintenance() -> None:
    dependency = Dependency.from_string("npm:babel/core@8.1.0")
    metadata = {"name": "@babel/core", "version": "8.1.0"}
    with (
        patch("it_depends.npm.resolve_executable", return_value="npm"),
        patch("it_depends.npm.subprocess.check_output", return_value=json.dumps(metadata).encode()),
    ):
        package = next(NPMResolver().resolve(dependency))
    repo = PackageRepository()
    repo.add(package)
    commits = [{"commit": {"committer": {"date": "2000-01-01T00:00:00Z"}}}]
    with patch("it_depends.maintenance.requests.get") as get:
        get.side_effect = [
            Mock(json=Mock(return_value={"repository": "https://github.com/babel/babel"})),
            Mock(json=Mock(return_value=commits)),
        ]
        check_maintenance_status(repo)
    assert get.call_args_list[0].args[0] == "https://registry.npmjs.org/%40babel%2Fcore"
    assert package.name == dependency.package == "babel/core"
    assert package.maintenance_info is not None
    assert package.maintenance_info.repository_url == "https://github.com/babel/babel"
    assert package.maintenance_info.error is None


@pytest.mark.parametrize(
    ("name", "registry"),
    [
        ("@babel/core", "https://registry.npmjs.org/%40babel%2Fcore"),
        ("lodash", "https://registry.npmjs.org/lodash"),
    ],
)
def test_npm_registry_names(name: str, registry: str) -> None:
    package = Package(name, "1.0.0", "npm")
    repo = PackageRepository()
    repo.add(package)
    response = Mock(json=Mock(return_value={}))
    with patch("it_depends.maintenance.requests.get", return_value=response) as get:
        check_maintenance_status(repo)
    get.assert_called_once()
    assert get.call_args.args[0] == registry
    assert package.name == name
