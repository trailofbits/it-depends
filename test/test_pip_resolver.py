"""Regression tests for release-specific pip dependency metadata."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from zipfile import ZipFile

import pytest
from johnnydep import JohnnyDist
from packaging.requirements import Requirement

from it_depends.dependencies import Dependency, SimpleSpec
from it_depends.pip import PipResolver, PipSourcePackage


@pytest.fixture
def registry(monkeypatch: pytest.MonkeyPatch) -> dict[str, dict[str, list[str]]]:
    releases = {
        "python-dateutil": {
            "2.8.1": ["six>=1.5"],
            "2.8.2": ["six>=1.5"],
            "3.0.0": ["tzdata"],
        },
        "six": {"1.5.0": []},
        "tzdata": {"2026.1": []},
    }

    def info(req: Requirement, *_args: object) -> SimpleNamespace:
        versions = [v for v in releases[req.name] if req.specifier.contains(v)]
        version = versions[-1]
        return SimpleNamespace(
            import_names=[],
            metadata={"version": version, "requires_dist": releases[req.name][version]},
            entry_points=[],
            sha256="test",
        )

    monkeypatch.setattr("johnnydep.lib._get_info", info)
    monkeypatch.setattr("johnnydep.lib._get_versions", lambda req, *_args: list(releases[req.name]))
    return releases


@pytest.mark.usefixtures("registry")
def test_constrained_versions_keep_their_own_dependencies() -> None:
    packages = list(PipResolver().resolve(Dependency.from_string("pip:python-dateutil@~=2.8.1")))
    assert {str(p.version) for p in packages} == {"2.8.1", "2.8.2"}
    assert all({d.package for d in p.dependencies} == {"six"} for p in packages)


@pytest.mark.usefixtures("registry")
def test_each_release_has_distinct_metadata() -> None:
    packages = list(PipResolver().resolve(Dependency.from_string("pip:python-dateutil@*")))
    assert {str(p.version): {d.package for d in p.dependencies} for p in packages} == {
        "2.8.1": {"six"},
        "2.8.2": {"six"},
        "3.0.0": {"tzdata"},
    }


def test_original_release_string_and_extras(registry: dict[str, dict[str, list[str]]]) -> None:
    registry["example"] = {"1.0.0.post1": ['six>=1.5; extra == "compat"'], "2.0.0": []}
    packages = list(PipResolver().resolve_dist(JohnnyDist("example[compat]"), recurse=False))
    assert len(packages) == len(registry["example"])
    assert {d.package for d in packages[0].dependencies} == {"six"}
    assert not packages[1].dependencies


def test_recursive_resolution_uses_release_metadata_and_terminates(
    registry: dict[str, dict[str, list[str]]],
) -> None:
    registry["six"]["1.5.0"] = ["python-dateutil==2.8.1"]
    packages = list(PipResolver().resolve_dist(JohnnyDist("python-dateutil"), version=SimpleSpec("~=2.8.1")))
    assert {(p.name, str(p.version)) for p in packages} == {
        ("python-dateutil", "2.8.1"),
        ("python-dateutil", "2.8.2"),
        ("six", "1.5.0"),
    }
    assert len(packages) == len({(p.name, str(p.version)) for p in packages})


@pytest.mark.usefixtures("registry")
def test_no_matching_versions() -> None:
    assert list(PipResolver().resolve(Dependency.from_string("pip:python-dateutil@<1"))) == []


@pytest.mark.usefixtures("registry")
def test_invalid_versions_are_skipped() -> None:
    dist = JohnnyDist("python-dateutil")
    dist.versions_available = ["invalid", "2.8.1"]
    packages = list(PipResolver().resolve_dist(dist, recurse=False))
    assert [str(p.version) for p in packages] == ["2.8.1"]
    assert {d.package for d in packages[0].dependencies} == {"six"}


@pytest.mark.usefixtures("registry")
def test_local_wheel_metadata_is_preserved(tmp_path: Path) -> None:
    wheel = tmp_path / "python_dateutil-2.8.1-py3-none-any.whl"
    with ZipFile(wheel, "w") as archive:
        archive.writestr("dateutil/__init__.py", "")
        archive.writestr(
            "python_dateutil-2.8.1.dist-info/METADATA",
            "Metadata-Version: 2.1\nName: python-dateutil\nVersion: 2.8.1\nRequires-Dist: tzdata\n",
        )
    dist = JohnnyDist(str(wheel))
    dist.versions_available = ["2.8.1"]
    _ = dist.children
    with patch("johnnydep.lib._get_info", side_effect=AssertionError("unexpected registry lookup")):
        packages = list(PipResolver().resolve_dist(dist, recurse=False))
    assert {d.package for d in packages[0].dependencies} == {"tzdata"}
    source = PipSourcePackage.from_dist(dist, tmp_path)
    assert {d.package for d in source.dependencies} == {"tzdata"}


@pytest.mark.usefixtures("registry")
@pytest.mark.parametrize("installed", [None, "2.8.1"])
def test_legacy_local_version(installed: str | None) -> None:
    dist = JohnnyDist("python-dateutil==2.8.1")
    dist.versions_available = ["none"]
    dist.version_installed = installed
    packages = list(PipResolver().resolve_dist(dist, recurse=False))
    assert [str(p.version) for p in packages] == ([] if installed is None else [installed])
    assert all({d.package for d in p.dependencies} == {"six"} for p in packages)


@pytest.mark.usefixtures("registry")
def test_release_metadata_failure_is_not_replaced_with_latest() -> None:
    dist = JohnnyDist("python-dateutil")
    with (
        patch("johnnydep.lib._get_info", side_effect=ValueError("metadata unavailable")),
        pytest.raises(ValueError, match="metadata unavailable"),
    ):
        list(PipResolver().resolve_dist(dist, version=SimpleSpec("==2.8.1"), recurse=False))
