"""Maintenance attribution for npm dependencies with local aliases."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import Mock, PropertyMock, patch

import pytest
from semantic_version import NpmSpec, Version

from it_depends.cache import PackageRepository
from it_depends.maintenance import check_maintenance_status
from it_depends.models import AliasedDependency, Dependency, Package, SourcePackage
from it_depends.npm import NPMResolver, generate_dependency_from_information
from it_depends.repository import SourceRepository

if TYPE_CHECKING:
    from collections.abc import Iterable


def _repo(dependencies: Iterable[Dependency], *packages: Package) -> PackageRepository:
    repo = PackageRepository()
    repo.add(
        SourcePackage(
            "root",
            Version("1.0.0"),
            SourceRepository("."),
            "npm",
            dependencies=dependencies,
        )
    )
    repo.extend(packages)
    return repo


def _alias(target: str, version: str = "*") -> AliasedDependency:
    return AliasedDependency("lodash", target, "npm", NpmSpec(version))


def _check(repo: PackageRepository) -> list[str]:
    metadata = {"repository": {"url": "https://github.com/babel/babel"}}
    commits = [{"commit": {"committer": {"date": "2000-01-01T00:00:00Z"}}}]

    def response(url: str, **_kwargs: object) -> Mock:
        data = commits if url.startswith("https://api.github.com/") else metadata
        return Mock(json=Mock(return_value=data))

    with patch("it_depends.maintenance.requests.get", side_effect=response) as get:
        check_maintenance_status(repo)
    return [call.args[0] for call in get.call_args_list]


def test_resolved_alias_uses_registry_target() -> None:
    dependency = generate_dependency_from_information("lodash", "npm:@babel/core@8.1.0")
    assert isinstance(dependency, AliasedDependency)
    with (
        patch.object(NPMResolver, "tool_path", new_callable=PropertyMock, return_value="npm"),
        patch(
            "it_depends.npm.subprocess.check_output",
            return_value=b'{"name":"@babel/core","version":"8.1.0","dependencies":{}}',
        ),
    ):
        package = next(NPMResolver().resolve(dependency))
    repo = _repo([dependency], package)

    urls = _check(repo)

    assert urls[0] == "https://registry.npmjs.org/%40babel%2Fcore"
    assert package.name == "lodash"
    assert list(repo.match(dependency)) == [package]
    assert package.maintenance_info is not None
    assert package.maintenance_info.repository_url == "https://github.com/babel/babel"
    assert package.maintenance_info.error is None


@pytest.mark.parametrize(
    "dependencies",
    [
        [_alias("babel/core"), _alias("babel/parser")],
        [_alias("babel/core"), Dependency("lodash", "npm", NpmSpec("8.1.0"))],
        [_alias("babel/core", "^7.0.0")],
    ],
)
def test_unverified_alias_target_remains_unknown(dependencies: list[Dependency]) -> None:
    package = Package("lodash", "8.1.0", "npm")

    urls = _check(_repo(dependencies, package))

    assert urls == []
    assert package.maintenance_info is not None
    assert package.maintenance_info.repository_url is None
    assert package.maintenance_info.is_stale is None
    assert "Cannot verify" in str(package.maintenance_info.error)
    assert "npm:lodash@8.1.0" in str(package.maintenance_info.error)


def test_alias_targets_are_selected_by_version() -> None:
    first = Package("lodash", "7.0.0", "npm")
    second = Package("lodash", "8.0.0", "npm")
    dependencies = [_alias("babel/core", "^7.0.0"), _alias("babel/parser", "^8.0.0")]

    urls = _check(_repo(dependencies, first, second))

    registries = [url for url in urls if url.startswith("https://registry.npmjs.org/")]
    assert registries == [
        "https://registry.npmjs.org/%40babel%2Fcore",
        "https://registry.npmjs.org/%40babel%2Fparser",
    ]
    for package in (first, second):
        assert package.maintenance_info is not None
        assert package.maintenance_info.error is None


def test_repeated_alias_target_is_unambiguous() -> None:
    package = Package("lodash", "8.1.0", "npm")
    dependencies = [_alias("babel/core", "^8.0.0"), _alias("babel/core", "8.1.0")]

    urls = _check(_repo(dependencies, package))

    assert urls[0] == "https://registry.npmjs.org/%40babel%2Fcore"
    assert package.maintenance_info is not None
    assert package.maintenance_info.error is None


def test_public_namesake_with_disjoint_alias_version_keeps_its_identity() -> None:
    package = Package("lodash", "4.17.21", "npm")
    dependencies = [_alias("babel/core", "^8.0.0"), Dependency("lodash", "npm", NpmSpec("^4.0.0"))]

    urls = _check(_repo(dependencies, package))

    assert urls[0] == "https://registry.npmjs.org/lodash"
    assert package.maintenance_info is not None
    assert package.maintenance_info.error is None


def test_alias_from_another_ecosystem_does_not_change_npm_identity() -> None:
    package = Package("lodash", "8.1.0", "npm")
    dependency = AliasedDependency("lodash", "babel/core", "pip")

    urls = _check(_repo([dependency], package))

    assert urls[0] == "https://registry.npmjs.org/lodash"
