"""Ubuntu package dependency resolver module."""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
from functools import lru_cache
from typing import TYPE_CHECKING

from it_depends.dependencies import (
    Dependency,
    DependencyResolver,
    Dict,
    List,
    Package,
    PackageCache,
    ResolverAvailability,
    SimpleSpec,
    SourcePackage,
    SourceRepository,
    Tuple,
    Version,
)
from it_depends.native import get_native_dependencies

from .apt import file_to_packages
from .docker import run_command

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator

logger = logging.getLogger(__name__)

# Constants
APT_RETURN_CODE_PACKAGE_NOT_FOUND = 100


class UbuntuResolver(DependencyResolver):
    """Expands dependencies based upon Ubuntu package dependencies."""

    name = "ubuntu"
    description = "expands dependencies based upon Ubuntu package dependencies"

    _pattern = re.compile(r" *(?P<package>[^ ]*)( *\((?P<version>.*)\))? *")
    _ubuntu_version = re.compile("([0-9]+:)*(?P<version>[^-]*)(-.*)*")

    @staticmethod
    @lru_cache(maxsize=2048)
    def ubuntu_packages(package_name: str) -> Iterable[Package]:
        """Iterate over all of the package versions available for a package name."""
        # Parses the dependencies of dependency.package out of the `apt show` command
        logger.debug("Running `apt show -a %s`", package_name)
        try:
            contents = run_command("apt", "show", "-a", package_name).decode("utf8")
        except subprocess.CalledProcessError as e:
            if e.returncode == APT_RETURN_CODE_PACKAGE_NOT_FOUND:
                contents = ""
            else:
                raise

        # Possibly means that the package does not appear ubuntu with the exact name
        if not contents:
            logger.warning("Package %s not found in ubuntu installed apt sources", package_name)
            return ()

        # Example depends line:
        # Depends: libc6 (>= 2.29), libgcc-s1 (>= 3.4), libstdc++6 (>= 9)
        version: Version | None = None
        packages: Dict[Tuple[str, Version], List[List[Dependency]]] = {}

        # Process version lines
        for line in contents.split("\n"):
            if line.startswith("Version: "):
                version = UbuntuResolver._parse_version_line(line, package_name)
                if version and (package_name, version) not in packages:
                    packages[(package_name, version)] = []
            elif version is not None and line.startswith("Depends: "):
                UbuntuResolver._parse_dependencies_line(line, package_name, version, packages)
                version = None

        # Sometimes `apt show` will return multiple packages with the same version but different dependencies.
        # For example: `apt show -a dkms`
        # Currently, we do a union over their dependencies
        # TODO: Figure out a better way to handle this # noqa: FIX002, TD002, TD003
        return [
            Package(
                name=pkg_name,
                version=version,
                source=UbuntuResolver(),
                dependencies=set().union(*duplicates),  # type: ignore[arg-type]
            )
            for (pkg_name, version), duplicates in packages.items()
        ]

    @staticmethod
    def _parse_version_line(line: str, package_name: str) -> Version | None:
        """Parse version information from apt output line."""
        matched = UbuntuResolver._ubuntu_version.match(line[len("Version: ") :])
        if matched:
            # TODO: Ubuntu versions can include "~", which the semantic_version # noqa: FIX002, TD002, TD003
            #       library does not like. So hack a fix by simply dropping everything after the tilde:
            raw_version = matched.group("version").split("~", maxsplit=1)[0]
            return Version.coerce(raw_version)
        logger.warning("Failed to parse package %s %s", package_name, line)
        return None

    @staticmethod
    def _parse_dependencies_line(
        line: str, package_name: str, version: Version, packages: Dict[Tuple[str, Version], List[List[Dependency]]]
    ) -> None:
        """Parse dependencies information from apt output line."""
        deps = []
        for dep in line[9:].split(","):
            for or_segment in dep.split("|"):
                # TODO: For now, treat each ORed dependency as a separate ANDed dependency # noqa: FIX002, TD002, TD003
                matched = UbuntuResolver._pattern.match(or_segment)
                if not matched:
                    error_msg = f"Invalid dependency line in apt output for {package_name}: {line!r}"
                    raise ValueError(error_msg)
                dep_package = matched.group("package")
                dep_version = matched.group("version")
                try:
                    # remove trailing ubuntu versions like "-10ubuntu4":
                    dep_version = dep_version.split("-", maxsplit=1)[0]
                    dep_version = dep_version.replace(" ", "")
                    SimpleSpec(dep_version.replace(" ", ""))
                except Exception:  # noqa: BLE001
                    dep_version = "*"  # Yolo FIXME Invalid simple block '= 1:7.0.1-12'

                deps.append((dep_package, dep_version))

        packages[(package_name, version)].append(
            [
                Dependency(
                    package=pkg,
                    semantic_version=SimpleSpec(ver),
                    source=UbuntuResolver(),
                )
                for pkg, ver in deps
            ]
        )

    def resolve(self, dependency: Dependency) -> Iterator[Package]:
        """Resolve a dependency to its packages."""
        if dependency.source != "ubuntu":
            error_msg = f"{self} can not resolve dependencies from other sources ({dependency})"
            raise ValueError(error_msg)

        if dependency.package.startswith("/"):
            # this is a file path, likely produced from native.py
            try:
                deps = [
                    Dependency(package=pkg_name, source=UbuntuResolver.name)
                    for pkg_name in file_to_packages(dependency.package)
                ]
                if deps:
                    yield Package(
                        name=dependency.package,
                        source=dependency.source,
                        version=Version.coerce("0"),
                        dependencies=deps,
                    )
            except (ValueError, subprocess.CalledProcessError):
                pass
        else:
            for package in UbuntuResolver.ubuntu_packages(dependency.package):
                if package.version in dependency.semantic_version:
                    yield package

    def __lt__(self, other: object) -> bool:
        """Make sure that the Ubuntu Classifier runs last."""
        return False

    def is_available(self) -> ResolverAvailability:
        """Check if the resolver is available."""
        if shutil.which("docker") is None:
            return ResolverAvailability(
                available=False,
                reason="`Ubuntu` classifier needs to have Docker installed. Try apt install docker.io.",
            )
        return ResolverAvailability(available=True)

    def can_resolve_from_source(self, _repo: SourceRepository) -> bool:
        """Check if the resolver can resolve from source."""
        return False

    def resolve_from_source(self, _repo: SourceRepository, _cache: PackageCache | None = None) -> SourcePackage | None:
        """Resolve from source repository."""
        return None

    def can_update_dependencies(self, package: Package) -> bool:
        """Check if dependencies can be updated."""
        return package.source != UbuntuResolver.name

    def update_dependencies(self, package: Package) -> Package:
        """Update package dependencies."""
        native_deps = get_native_dependencies(package)
        package.dependencies = package.dependencies.union(frozenset(native_deps))
        return package
