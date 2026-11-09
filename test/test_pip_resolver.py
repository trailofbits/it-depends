"""Regression tests for release-specific pip dependency metadata."""

import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from zipfile import BadZipFile, ZipFile

import pytest
from johnnydep import JohnnyDist, JohnnyError, lib
from packaging.markers import default_environment
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
@pytest.mark.parametrize("wheel_version", ["2.8.1", "1.0", "1.0.0", "1.0.0.post1"])
def test_local_wheel_metadata_is_preserved(tmp_path: Path, wheel_version: str) -> None:
    wheel = tmp_path / f"python_dateutil-{wheel_version}-py3-none-any.whl"
    with ZipFile(wheel, "w") as archive:
        archive.writestr("dateutil/__init__.py", "")
        archive.writestr(
            f"python_dateutil-{wheel_version}.dist-info/METADATA",
            f"Metadata-Version: 2.1\nName: python-dateutil\nVersion: {wheel_version}\nRequires-Dist: tzdata\n",
        )
    dist = JohnnyDist(str(wheel))
    _ = dist.children
    with (
        patch("johnnydep.lib._get_versions", return_value=[]),
        patch("johnnydep.lib._get_info", side_effect=AssertionError("unexpected registry lookup")),
    ):
        packages = list(PipResolver().resolve_dist(dist, recurse=False))
    assert len(packages) == 1
    assert {d.package for d in packages[0].dependencies} == {"tzdata"}
    source = PipSourcePackage.from_dist(dist, tmp_path)
    assert {d.package for d in source.dependencies} == {"tzdata"}


@pytest.mark.parametrize("recurse", [False, True])
def test_pinned_releases_preserve_index_and_environment(
    registry: dict[str, dict[str, list[str]]], *, recurse: bool
) -> None:
    registry["example"] = {
        "1.0.0": ['six; sys_platform == "win32"'],
        "2.0.0": ['tzdata; sys_platform == "win32"'],
    }
    index_urls = ("https://packages.example.invalid/simple",)
    env = tuple(sorted({**default_environment(), "sys_platform": "win32"}.items()))
    get_info = lib._get_info  # noqa: SLF001 -- mock only the registry lookup boundary.

    def info(req: Requirement, indexes: tuple[str, ...], environment: object) -> SimpleNamespace:
        assert indexes == index_urls
        assert environment == env
        return get_info(req, indexes, environment)

    with patch("johnnydep.lib._get_info", side_effect=info):
        dist = JohnnyDist("example", index_urls=index_urls, env=env)
        packages = list(PipResolver().resolve_dist(dist, recurse=recurse))
    assert {str(p.version): {d.package for d in p.dependencies} for p in packages if p.name == "example"} == {
        "1.0.0": {"six"},
        "2.0.0": {"tzdata"},
    }
    assert {p.name for p in packages} == ({"example", "six", "tzdata"} if recurse else {"example"})


def test_equivalent_versions_are_deduplicated(registry: dict[str, dict[str, list[str]]]) -> None:
    registry["example"] = {"1": [], "1.0": [], "1.0.0": [], "1.0.0.post1": [], "1.0.0.post2": []}
    packages = list(PipResolver().resolve_dist(JohnnyDist("example"), recurse=False))
    assert [str(p.version) for p in packages] == ["1.0.0", "1.0.0+post1", "1.0.0+post2"]


def test_recursive_resolution_preserves_distinct_extras(registry: dict[str, dict[str, list[str]]]) -> None:
    registry["root"] = {"1.0.0": ["example[compat]", "example[other]"]}
    registry["example"] = {
        "1.0.0": ['six; extra == "compat"', 'tzdata; extra == "other"'],
    }
    packages = list(PipResolver().resolve_dist(JohnnyDist("root")))
    example_dependencies = [frozenset(d.package for d in p.dependencies) for p in packages if p.name == "example"]
    expected = {frozenset({"six"}), frozenset({"tzdata"})}
    assert len(example_dependencies) == len(expected)
    assert set(example_dependencies) == expected
    assert {p.name for p in packages} == {"root", "example", "six", "tzdata"}


@pytest.mark.usefixtures("registry")
@pytest.mark.parametrize("metadata_version", [None, "invalid"])
def test_missing_or_invalid_metadata_version_is_refetched(metadata_version: str | None) -> None:
    dist = JohnnyDist("python-dateutil")
    dist.metadata["version"] = metadata_version
    packages = list(PipResolver().resolve_dist(dist, recurse=False))
    assert {str(p.version): {d.package for d in p.dependencies} for p in packages} == {
        "2.8.1": {"six"},
        "2.8.2": {"six"},
        "3.0.0": {"tzdata"},
    }


@pytest.mark.usefixtures("registry")
def test_invalid_python_version_does_not_discard_other_releases(caplog: pytest.LogCaptureFixture) -> None:
    dist = JohnnyDist("python-dateutil")
    dist.versions_available = ["2.8.1", "1.0.0-unknown", "2.8.2"]
    packages = list(PipResolver().resolve_dist(dist, recurse=False))
    assert [str(p.version) for p in packages] == ["2.8.1", "2.8.2"]
    assert "python-dateutil==1.0.0-unknown" in caplog.text


@pytest.mark.usefixtures("registry")
@pytest.mark.parametrize("ignore_errors", [False, True])
def test_release_metadata_errors_follow_supplied_policy(*, ignore_errors: bool) -> None:
    get_info = lib._get_info  # noqa: SLF001 -- mock only the registry lookup boundary.

    def info(req: Requirement, *args: object) -> SimpleNamespace:
        if str(req.specifier) == "==2.8.1":
            message = "release metadata unavailable"
            raise ValueError(message)
        return get_info(req, *args)

    with patch("johnnydep.lib._get_info", side_effect=info):
        dist = JohnnyDist("python-dateutil", ignore_errors=ignore_errors)
        packages = list(PipResolver().resolve_dist(dist, recurse=False))
    expected = {"2.8.2": {"six"}, "3.0.0": {"tzdata"}}
    if ignore_errors:
        expected["2.8.1"] = set()
    assert {str(p.version): {d.package for d in p.dependencies} for p in packages} == expected


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
@pytest.mark.parametrize(
    "error",
    [
        ValueError("invalid metadata"),
        subprocess.CalledProcessError(1, ["pip", "wheel"]),
        JohnnyError("no wheel"),
        HTTPError("https://packages.example.invalid/release.whl", 404, "Not Found", None, None),
        URLError("offline"),
        BadZipFile("invalid wheel"),
    ],
)
@pytest.mark.parametrize("failed_version", ["2.8.1", "2.8.2"])
def test_public_resolver_retains_releases_after_metadata_failure(
    error: Exception,
    failed_version: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    get_info = lib._get_info  # noqa: SLF001

    def info(req: Requirement, *args: object) -> SimpleNamespace:
        if str(req.specifier) == f"=={failed_version}":
            raise error
        return get_info(req, *args)

    with patch("johnnydep.lib._get_info", side_effect=info):
        packages = list(PipResolver().resolve(Dependency.from_string("pip:python-dateutil@*")))
    expected = {"2.8.1": {"six"}, "2.8.2": {"six"}, "3.0.0": {"tzdata"}}
    del expected[failed_version]
    assert {str(p.version): {d.package for d in p.dependencies} for p in packages} == expected
    assert f"python-dateutil=={failed_version}" in caplog.text
    assert "Skipping" in caplog.text


@pytest.mark.usefixtures("registry")
def test_public_resolver_retains_releases_after_dependency_metadata_failure(
    caplog: pytest.LogCaptureFixture,
) -> None:
    get_dependencies = PipResolver.get_dependencies

    def dependencies(dist: JohnnyDist) -> object:
        if dist.metadata["version"] == "2.8.2":
            message = "child metadata unavailable"
            raise ValueError(message)
        return get_dependencies(dist)

    with patch.object(PipResolver, "get_dependencies", side_effect=dependencies):
        packages = list(PipResolver().resolve(Dependency.from_string("pip:python-dateutil@*")))
    assert [str(p.version) for p in packages] == ["2.8.1", "3.0.0"]
    assert "python-dateutil==2.8.2" in caplog.text


@pytest.mark.usefixtures("registry")
def test_all_failed_releases_return_empty_with_diagnostics(caplog: pytest.LogCaptureFixture) -> None:
    with patch.object(PipResolver, "get_dependencies", side_effect=ValueError("metadata unavailable")):
        packages = list(PipResolver().resolve(Dependency.from_string("pip:python-dateutil@*")))
    assert packages == []
    for version in ("2.8.1", "2.8.2", "3.0.0"):
        assert f"python-dateutil=={version}" in caplog.text
