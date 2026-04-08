"""Tests for resolving projects with zero dependencies."""

from pathlib import Path

from it_depends.dependencies import SourceRepository
from it_depends.npm import NPMResolver
from it_depends.pip import PipResolver, PipSourcePackage


def test_npm_no_dependencies(tmp_path: Path) -> None:
    """An npm package.json with no dependencies field resolves to zero deps."""
    (tmp_path / "package.json").write_text('{"name": "empty-pkg", "version": "1.0.0"}')
    repo = SourceRepository(tmp_path)
    pkg = NPMResolver.from_package_json(repo)
    assert pkg.name == "empty-pkg"
    assert len(pkg.dependencies) == 0


def test_pip_empty_requirements_txt(tmp_path: Path) -> None:
    """An empty requirements.txt resolves to zero deps."""
    (tmp_path / "requirements.txt").write_text("")
    repo = SourceRepository(tmp_path)
    pkg = PipSourcePackage.from_repo(repo)
    assert pkg.name == tmp_path.name
    assert len(pkg.dependencies) == 0


def test_pip_whitespace_only_requirements_txt(tmp_path: Path) -> None:
    """A requirements.txt with only whitespace resolves to zero deps."""
    (tmp_path / "requirements.txt").write_text("\n\n   \n")
    repo = SourceRepository(tmp_path)
    pkg = PipSourcePackage.from_repo(repo)
    assert len(pkg.dependencies) == 0


def test_pip_get_dependencies_empty(tmp_path: Path) -> None:
    """PipResolver.get_dependencies on empty requirements.txt returns no deps."""
    (tmp_path / "requirements.txt").write_text("")
    deps = list(PipResolver.get_dependencies(tmp_path))
    assert deps == []
