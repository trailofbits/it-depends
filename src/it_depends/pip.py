"""Pip package dependency resolution."""

from __future__ import annotations

import io
import subprocess
import sys
from logging import getLogger
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator

from johnnydep import JohnnyDist
from johnnydep.logs import configure_logging

from .dependencies import (
    Dependency,
    DependencyResolver,
    DockerSetup,
    Package,
    SemanticVersion,
    SimpleSpec,
    SourcePackage,
    SourceRepository,
    Version,
)

configure_logging(1)
log = getLogger(__name__)


class PipResolver(DependencyResolver):
    """Resolver for Python packages using pip."""

    name = "pip"
    description = "classifies the dependencies of Python packages using pip"

    def can_resolve_from_source(self, repo: SourceRepository) -> bool:
        """Check if this resolver can resolve from the given source repository."""
        return (
            (self.is_available() and (repo.path / "setup.py").exists())
            or (repo.path / "requirements.txt").exists()
            or (repo.path / "pyproject.toml").exists()
        )

    def resolve_from_source(self, repo: SourceRepository, cache: object | None = None) -> SourcePackage | None:  # noqa: ARG002
        """Resolve package from source repository."""
        if not self.can_resolve_from_source(repo):
            return None
        return PipSourcePackage.from_repo(repo)

    def docker_setup(self) -> DockerSetup | None:
        """Get Docker setup configuration for pip resolver."""
        return DockerSetup(
            apt_get_packages=["python3", "python3-pip", "python3-dev", "gcc"],
            install_package_script="""#!/usr/bin/env bash
    pip3 install $1==$2
    """,
            load_package_script="""#!/usr/bin/env bash
    python3 -c "import $1"
    """,
            baseline_script='#!/usr/bin/env python3 -c ""\n',
        )

    @staticmethod
    def _get_specifier(dist_or_str: JohnnyDist | str) -> SimpleSpec:
        """Get a SimpleSpec from a JohnnyDist or string.

        Args:
            dist_or_str: JohnnyDist instance or string specifier

        Returns:
            SimpleSpec instance

        """
        if isinstance(dist_or_str, JohnnyDist):
            dist_or_str = dist_or_str.specifier
        try:
            return SimpleSpec(dist_or_str)
        except ValueError:
            return SimpleSpec("*")

    @staticmethod
    def parse_requirements_txt_line(line: str) -> Dependency | None:
        """Parse a single line from requirements.txt.

        Args:
            line: Line from requirements.txt

        Returns:
            Dependency instance or None if line is empty/invalid

        """
        line = line.strip()
        if not line:
            return None
        for possible_delimiter in ("=", "<", ">", "~", "!"):
            delimiter_pos = line.find(possible_delimiter)
            if delimiter_pos >= 0:
                break
        if delimiter_pos < 0:
            # the requirement does not have a version specifier
            name = line
            version = SimpleSpec("*")
        else:
            name = line[:delimiter_pos]
            version = PipResolver._get_specifier(line[delimiter_pos:])
        return Dependency(package=name, semantic_version=version, source=PipResolver())

    @staticmethod
    def _get_lower_bound(spec: SemanticVersion) -> Version | None:
        """Extract the lower bound version from a spec if it's a simple >= or > constraint.

        Args:
            spec: Semantic version specification

        Returns:
            The lower bound Version, or None if not a simple lower-bound constraint

        """
        clause = spec.clause
        if hasattr(clause, "operator") and hasattr(clause, "target") and (clause.operator in (">=", ">")):
            return clause.target
        return None

    @staticmethod
    def _merge_dependencies(deps: Iterable[Dependency]) -> list[Dependency]:
        """Merge dependencies with the same package name, keeping the least restrictive.

        When multiple dependencies exist for the same package (e.g., due to environment
        markers), this keeps the one with the lowest version constraint (least restrictive).

        Args:
            deps: Iterable of dependencies to merge

        Returns:
            List of merged dependencies with duplicates removed

        """
        by_package: dict[str, Dependency] = {}
        for dep in deps:
            key = dep.package
            if key not in by_package:
                by_package[key] = dep
            else:
                existing = by_package[key]
                existing_lb = PipResolver._get_lower_bound(existing.semantic_version)
                new_lb = PipResolver._get_lower_bound(dep.semantic_version)

                if existing_lb is not None and new_lb is not None:
                    # Keep the one with lower bound (less restrictive)
                    if new_lb < existing_lb:
                        by_package[key] = dep
                elif new_lb is None and existing_lb is not None:
                    # Prefer wildcard/complex specs as they may be less restrictive
                    by_package[key] = dep
                # Otherwise keep existing

        return list(by_package.values())

    @staticmethod
    def get_dependencies(
        dist_or_requirements_txt_path: JohnnyDist | Path | str,
    ) -> Iterable[Dependency]:
        """Get dependencies from a distribution or requirements.txt file.

        Args:
            dist_or_requirements_txt_path: JohnnyDist, Path, or string path

        Returns:
            Iterable of Dependency instances

        """
        if isinstance(dist_or_requirements_txt_path, JohnnyDist):
            all_deps = [
                Dependency(
                    package=child.name,
                    semantic_version=PipResolver._get_specifier(child),
                    source=PipResolver(),
                )
                for child in dist_or_requirements_txt_path.children
            ]
            return PipResolver._merge_dependencies(all_deps)
        if isinstance(dist_or_requirements_txt_path, str):
            dist_or_requirements_txt_path = Path(dist_or_requirements_txt_path)
        with (dist_or_requirements_txt_path / "requirements.txt").open() as f:
            deps = [d for d in (PipResolver.parse_requirements_txt_line(line) for line in f) if d is not None]
            return PipResolver._merge_dependencies(deps)

    @staticmethod
    def get_version(version_str: str, none_default: Version | None = None) -> Version | None:
        """Parse a version string into a Version object.

        Args:
            version_str: Version string to parse
            none_default: Default version if version_str is "none"

        Returns:
            Version object or None if parsing fails

        """
        if version_str == "none":
            # this will happen if the dist is for a local wheel:
            return none_default
        try:
            return Version.coerce(version_str)
        except ValueError:
            components = version_str.split(".")
            version_components_count = 4
            if len(components) == version_components_count:
                try:
                    # assume the version component after the last period is the release
                    return Version(
                        major=int(components[0]),
                        minor=int(components[1]),
                        patch=int(components[2]),
                        prerelease=components[3],
                    )
                except ValueError:
                    pass
            # TODO(@evandowning): Figure out a better way to handle invalid version strings  # noqa: TD003, FIX002
        return None

    def resolve_dist(
        self,
        dist: JohnnyDist,
        *,  # Force keyword-only arguments
        recurse: bool = True,
        version: SemanticVersion | None = None,
    ) -> Iterable[Package]:
        """Resolve packages from a JohnnyDist.

        Args:
            dist: JohnnyDist to resolve
            recurse: Whether to recursively resolve dependencies
            version: Version specification to filter by

        Returns:
            Iterable of Package instances

        """
        if version is None:
            version = SimpleSpec("*")
        queue = [(dist, version)]
        packages: list[Package] = []
        while queue:
            dist, sem_version = queue.pop()
            none_default = Version.coerce(dist.version_installed) if dist.version_installed is not None else None
            for pkg_version in sem_version.filter(
                filter(
                    lambda v: v is not None,
                    (PipResolver.get_version(v_str, none_default=none_default) for v_str in dist.versions_available),
                )
            ):
                package = Package(
                    name=dist.name,
                    version=pkg_version,
                    dependencies=self.get_dependencies(dist),
                    source=self,
                )
                packages.append(package)
                if not recurse:
                    break
                queue.extend((child, self._get_specifier(child)) for child in dist.children)
        return packages

    def resolve(self, dependency: Dependency) -> Iterator[Package]:
        """Resolve a dependency to packages.

        Args:
            dependency: Dependency to resolve

        Yields:
            Package instances that satisfy the dependency

        """
        try:
            return iter(
                self.resolve_dist(
                    JohnnyDist(f"{dependency.package}"),
                    version=dependency.semantic_version,
                    recurse=False,
                )
            )
        except subprocess.CalledProcessError:
            log.exception("Error using JohnnyDep to resolve %s", dependency.package)
            return iter(())
        except ValueError as e:
            log.warning(str(e))
            return iter(())


class PipSourcePackage(SourcePackage):
    """Source package for Python packages."""

    @staticmethod
    def from_dist(dist: JohnnyDist, source_path: Path) -> PipSourcePackage:
        """Create a PipSourcePackage from a JohnnyDist.

        Args:
            dist: JohnnyDist instance
            source_path: Path to source directory

        Returns:
            PipSourcePackage instance

        """
        version_str = dist.specifier
        version_str = version_str.removeprefix("==")
        return PipSourcePackage(
            name=dist.name,
            version=PipResolver.get_version(version_str),
            dependencies=PipResolver.get_dependencies(dist),
            source_repo=SourceRepository(source_path),
            source="pip",
        )

    @staticmethod
    def from_repo(repo: SourceRepository) -> PipSourcePackage:
        """Create a PipSourcePackage from a source repository.

        Args:
            repo: Source repository

        Returns:
            PipSourcePackage instance

        """
        if (repo.path / "setup.py").exists() or (repo.path / "pyproject.toml").exists():
            with TemporaryDirectory() as tmp_dir:
                try:
                    _ = sys.stderr.fileno()
                    stderr = sys.stderr
                except io.UnsupportedOperation:
                    stderr = None
                subprocess.check_call(  # noqa: S603
                    [
                        sys.executable,
                        "-m",
                        "pip",
                        "wheel",
                        "--no-deps",
                        "-w",
                        tmp_dir,
                        str(repo.path.absolute()),
                    ],
                    stdout=stderr,
                )
                wheel = None
                for whl in Path(tmp_dir).glob("*.whl"):
                    if wheel is not None:
                        msg = f"`pip wheel --no-deps {repo.path!s}` produced multiple wheel files!"
                        raise ValueError(msg)
                    wheel = whl
                if wheel is None:
                    msg = f"`pip wheel --no-deps {repo.path!s}` did not produce a wheel file!"
                    raise ValueError(msg)
                dist = JohnnyDist(str(wheel))
                # force JohnnyDist to read the dependencies before deleting the wheel:
                _ = dist.children
                return PipSourcePackage.from_dist(dist, repo.path)
        elif (repo.path / "requirements.txt").exists():
            # We just have a requirements.txt and no setup.py
            # Use the directory name as the package name
            name = repo.path.absolute().name
            if (repo.path / "VERSION").exists():
                with (repo.path / "VERSION").open() as f:
                    version = PipResolver.get_version(f.read().strip())
            else:
                version = PipResolver.get_version("0.0.0")
                log.info("Could not detect %s version. Using: %s", repo.path, version)
            return PipSourcePackage(
                name=name,
                version=version,
                dependencies=PipResolver.get_dependencies(repo.path),
                source_repo=repo,
                source="pip",
            )
        else:
            msg = f"{repo.path} neither has a setup.py, requirements.txt, nor pyproject.toml"
            raise ValueError(msg)
