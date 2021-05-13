from logging import getLogger
from pathlib import Path
from tempfile import TemporaryDirectory
import subprocess
import sys
from typing import Iterable, Iterator, List, Optional

from johnnydep import JohnnyDist
from johnnydep.logs import configure_logging

from .dependencies import (
    Dependency, DependencyClassifier, DependencyResolver, DockerSetup, Package, PackageCache, SemanticVersion,
    SimpleSpec, SourcePackage, SourceRepository, Version
)


configure_logging(1)
log = getLogger(__file__)


class PipResolver(DependencyResolver):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs, source=PipClassifier())

    @staticmethod
    def _get_specifier(dist: JohnnyDist) -> SimpleSpec:
        try:
            return SimpleSpec(dist.specifier)
        except ValueError:
            return SimpleSpec("*")

    @staticmethod
    def get_dependencies(dist: JohnnyDist) -> Iterable[Dependency]:
        return (
            Dependency(package=child.name, semantic_version=PipResolver._get_specifier(child), source=PipClassifier())
            for child in dist.children
        )

    @staticmethod
    def get_version(version_str: str, none_default: Optional[Version] = None) -> Optional[Version]:
        if version_str == "none":
            # this will happen if the dist is for a local wheel:
            return none_default
        else:
            try:
                return Version.coerce(version_str)
            except ValueError:
                components = version_str.split(".")
                if len(components) == 4:
                    try:
                        # assume the version component after the last period is the release
                        return Version(
                            major=int(components[0]),
                            minor=int(components[1]),
                            patch=int(components[2]),
                            prerelease=components[3]
                        )
                    except ValueError:
                        pass
                # TODO: Figure out a better way to handle invalid version strings
            return None

    def resolve_dist(
            self, dist: JohnnyDist, recurse: bool = True, version: SemanticVersion = SimpleSpec("*")
    ) -> Iterable[Package]:
        queue = [(dist, version)]
        packages: List[Package] = []
        while queue:
            dist, sem_version = queue.pop()
            if dist.version_installed is not None:
                none_default = Version.coerce(dist.version_installed)
            else:
                none_default = None
            for version in sem_version.filter(
                    filter(
                        lambda v: v is not None,
                        (
                                PipResolver.get_version(v_str, none_default=none_default)
                                for v_str in dist.versions_available
                        )
                    )
            ):
                cached = self.resolve_from_cache(Dependency(package=dist.name,
                                                            semantic_version=version,
                                                            source=PipClassifier()))
                if cached is not None:
                    packages.extend(cached)
                else:
                    package = Package(
                        name=dist.name,
                        version=version,
                        dependencies=self.get_dependencies(dist),
                        source=PipClassifier()
                    )
                    packages.append(package)
                if not recurse:
                    break
                queue.extend((child, self._get_specifier(child)) for child in dist.children)
        return packages

    def resolve_missing(self, dependency: Dependency, from_package: Package) -> Iterator[Package]:
        try:
            return iter(self.resolve_dist(
                JohnnyDist(f"{dependency.package}{dependency.semantic_version}"), version=dependency.semantic_version,
                recurse=False
            ))
        except ValueError as e:
            log.warning(str(e))
            return iter(())


class PipSourcePackage(SourcePackage):
    def __init__(self, dist: JohnnyDist, source_path: Path):
        version_str = dist.specifier
        if version_str.startswith("=="):
            version_str = version_str[2:]
        super().__init__(name=dist.name, version=PipResolver.get_version(version_str),
                         dependencies=PipResolver.get_dependencies(dist), source_path=source_path,
                         source=PipClassifier())

    @staticmethod
    def from_repo(repo: SourceRepository) -> "PipSourcePackage":
        with TemporaryDirectory() as tmp_dir:
            subprocess.check_call([
                sys.executable, "-m", "pip", "wheel", "--no-deps", "-w", tmp_dir, str(repo.path)
            ], stdout=sys.stderr)
            wheel = None
            for whl in Path(tmp_dir).glob("*.whl"):
                if wheel is not None:
                    raise ValueError(f"`pip wheel --no-deps {repo.path!s}` produced mutliple wheel files!")
                wheel = whl
            if wheel is None:
                raise ValueError(f"`pip wheel --no-deps {repo.path!s}` did not produce a wheel file!")
            dist = JohnnyDist(str(wheel))
            # force JohnnyDist to read the dependencies before deleting the wheel:
            _ = dist.children
            return PipSourcePackage(dist, repo.path)


class PipClassifier(DependencyClassifier):
    name = "pip"
    description = "classifies the dependencies of Python packages using pip"

    def can_classify(self, repo: SourceRepository) -> bool:
        return (repo.path / "setup.py").exists() or (repo.path / "requirements.txt").exists()

    def classify(self, repo: SourceRepository, cache: Optional[PackageCache] = None):
        resolver = PipResolver(cache=cache)
        repo.resolvers.append(resolver)
        repo.add(PipSourcePackage.from_repo(repo))
        resolver.resolve_unsatisfied(repo, max_workers=1)  # Johnny Dep doesn't like concurrency

    def docker_setup(self) -> Optional[DockerSetup]:
        return DockerSetup(
            apt_get_packages=["python3", "python3-pip", "python3-dev", "gcc"],
            install_package_script="""#!/usr/bin/env bash
pip3 install $1==$2
""",
            load_package_script="""#!/usr/bin/env bash
python3 -c "import $1"
""",
            baseline_script="#!/usr/bin/env python3 -c \"\"\n"
        )
