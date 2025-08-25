from functools import lru_cache
import shutil
import subprocess
import logging
import re
from typing import Iterable, Iterator, Optional

from .apt import file_to_packages
from .docker import is_running_ubuntu, run_command
from ..dependencies import (
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
from ..native import get_native_dependencies

logger = logging.getLogger(__name__)


class UbuntuResolver(DependencyResolver):
    name = "ubuntu"
    description = "expands dependencies based upon Ubuntu package dependencies"

    _pattern = re.compile(r" *(?P<package>[^ ]*)( *\((?P<version>.*)\))? *")
    _ubuntu_version = re.compile("([0-9]+:)*(?P<version>[^-]*)(-.*)*")

    @staticmethod
    @lru_cache(maxsize=2048)
    def ubuntu_packages(package_name: str) -> Iterable[Package]:
        """Iterates over all of the package versions available for a package name"""
        # Parses the dependencies of dependency.package out of the `apt show` command
        logger.debug(f"Running `apt show -a {package_name}`")
        try:
            contents = run_command("apt", "show", "-a", package_name).decode("utf8")
        except subprocess.CalledProcessError as e:
            if e.returncode == 100:
                contents = ""
            else:
                raise

        # Possibly means that the package does not appear ubuntu with the exact name
        if not contents:
            logger.warning(f"Package {package_name} not found in ubuntu installed apt sources")
            return ()

        # Example depends line:
        # Depends: libc6 (>= 2.29), libgcc-s1 (>= 3.4), libstdc++6 (>= 9)
        version: Optional[Version] = None
        packages: Dict[Tuple[str, Version], List[List[Dependency]]] = {}
        for line in contents.split("\n"):
            if line.startswith("Version: "):
                matched = UbuntuResolver._ubuntu_version.match(line[len("Version: ") :])
                if matched:
                    # FIXME: Ubuntu versions can include "~", which the semantic_version library does not like
                    #        So hack a fix by simply dropping everything after the tilde:
                    raw_version = matched.group("version").split("~", maxsplit=1)[0]
                    version = Version.coerce(raw_version)
                    if (package_name, version) not in packages:
                        packages[(package_name, version)] = []
                else:
                    logger.warning(f"Failed to parse package {package_name} {line}")
            elif version is not None and line.startswith("Depends: "):
                deps = []
                for dep in line[9:].split(","):
                    for or_segment in dep.split("|"):
                        # Fixme: For now, treat each ORed dependency as a separate ANDed dependency
                        matched = UbuntuResolver._pattern.match(or_segment)
                        if not matched:
                            raise ValueError(
                                f"Invalid dependency line in apt output for {package_name}: {line!r}"
                            )
                        dep_package = matched.group("package")
                        dep_version = matched.group("version")
                        try:
                            # remove trailing ubuntu versions like "-10ubuntu4":
                            dep_version = dep_version.split("-", maxsplit=1)[0]
                            dep_version = dep_version.replace(" ", "")
                            SimpleSpec(dep_version.replace(" ", ""))
                        except Exception as e:
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
                version = None

        # Sometimes `apt show` will return multiple packages with the same version but different dependencies.
        # For example: `apt show -a dkms`
        # Currently, we do a union over their dependencies
        # TODO: Figure out a better way to handle this
        return [
            Package(
                name=pkg_name,
                version=version,
                source=UbuntuResolver(),
                dependencies=set().union(*duplicates),  # type: ignore
            )
            for (pkg_name, version), duplicates in packages.items()
        ]

    def resolve(self, dependency: Dependency) -> Iterator[Package]:
        if dependency.source != "ubuntu":
            raise ValueError(
                f"{self} can not resolve dependencies from other sources ({dependency})"
            )

        if dependency.package.startswith("/"):
            # this is a file path, likely produced from native.py
            try:
                deps = []
                for pkg_name in file_to_packages(dependency.package):
                    deps.append(Dependency(package=pkg_name, source=UbuntuResolver.name))
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

    def __lt__(self, other):
        """Make sure that the Ubuntu Classifier runs last"""
        return False

    def is_available(self) -> ResolverAvailability:
        if shutil.which("docker") is None:
            return ResolverAvailability(
                False,
                "`Ubuntu` classifier needs to have Docker installed. Try apt install docker.io.",
            )
        return ResolverAvailability(True)

    def can_resolve_from_source(self, repo: SourceRepository) -> bool:
        return False

    def resolve_from_source(
        self, repo: SourceRepository, cache: Optional[PackageCache] = None
    ) -> Optional[SourcePackage]:
        return None

    def can_update_dependencies(self, package: Package) -> bool:
        return package.source != UbuntuResolver.name

    def update_dependencies(self, package: Package) -> Package:
        native_deps = get_native_dependencies(package)
        package.dependencies = package.dependencies.union(frozenset(native_deps))
        return package
