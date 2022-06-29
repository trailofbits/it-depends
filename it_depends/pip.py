from logging import getLogger
from pathlib import Path
from tempfile import TemporaryDirectory
import subprocess
import sys
from typing import Iterable, Iterator, List, Optional, Union

from johnnydep import JohnnyDist
from johnnydep.logs import configure_logging

from .dependencies import (
    Dependency,
    DependencyResolver,
    DockerSetup,
    Package,
    PackageCache,
    SemanticVersion,
    SimpleSpec,
    SourcePackage,
    SourceRepository,
    Version,
)


configure_logging(1)
log = getLogger(__file__)


class PipResolver(DependencyResolver):
    name = "pip"
    description = "classifies the dependencies of Python packages using pip"

    def can_resolve_from_source(self, repo: SourceRepository) -> bool:
        return (
            self.is_available
            and (repo.path / "setup.py").exists()
            or (repo.path / "requirements.txt").exists()
        )

    def resolve_from_source(
        self, repo: SourceRepository, cache: Optional[PackageCache] = None
    ) -> Optional[SourcePackage]:
        if not self.can_resolve_from_source(repo):
            return None
        return PipSourcePackage.from_repo(repo)

    def docker_setup(self) -> Optional[DockerSetup]:
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
    def _get_specifier(dist_or_str: Union[JohnnyDist, str]) -> SimpleSpec:
        if isinstance(dist_or_str, JohnnyDist):
            dist_or_str = dist_or_str.specifier
        try:
            return SimpleSpec(dist_or_str)
        except ValueError:
            return SimpleSpec("*")

    @staticmethod
    def parse_requirements_txt_line(line: str) -> Optional[Dependency]:
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
    def get_dependencies(
        dist_or_requirements_txt_path: Union[JohnnyDist, Path, str]
    ) -> Iterable[Dependency]:
        if isinstance(dist_or_requirements_txt_path, JohnnyDist):
            return (
                Dependency(
                    package=child.name,
                    semantic_version=PipResolver._get_specifier(child),
                    source=PipResolver(),
                )
                for child in dist_or_requirements_txt_path.children
            )
        elif isinstance(dist_or_requirements_txt_path, str):
            dist_or_requirements_txt_path = Path(dist_or_requirements_txt_path)
        with open(dist_or_requirements_txt_path / "requirements.txt", "r") as f:
            return filter(
                lambda d: d is not None,
                (
                    PipResolver.parse_requirements_txt_line(line)  # type: ignore
                    for line in f.readlines()
                ),
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
                            prerelease=components[3],
                        )
                    except ValueError:
                        pass
                # TODO: Figure out a better way to handle invalid version strings
            return None

    def resolve_dist(
        self,
        dist: JohnnyDist,
        recurse: bool = True,
        version: SemanticVersion = SimpleSpec("*"),
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
                    ),
                )
            ):
                package = Package(
                    name=dist.name,
                    version=version,
                    dependencies=self.get_dependencies(dist),
                    source=self,
                )
                packages.append(package)
                if not recurse:
                    break
                queue.extend((child, self._get_specifier(child)) for child in dist.children)
        return packages

    def resolve(self, dependency: Dependency) -> Iterator[Package]:
        print(dependency)
        try:
            return iter(
                self.resolve_dist(
                    JohnnyDist(f"{dependency.package}"),
                    version=dependency.semantic_version,
                    recurse=False,
                )
            )
        except ValueError as e:
            log.warning(str(e))
            return iter(())


class PipSourcePackage(SourcePackage):
    @staticmethod
    def from_dist(dist: JohnnyDist, source_path: Path) -> "PipSourcePackage":
        version_str = dist.specifier
        if version_str.startswith("=="):
            version_str = version_str[2:]
        return PipSourcePackage(
            name=dist.name,
            version=PipResolver.get_version(version_str),
            dependencies=PipResolver.get_dependencies(dist),
            source_repo=SourceRepository(source_path),
            source="pip",
        )

    @staticmethod
    def from_repo(repo: SourceRepository) -> "PipSourcePackage":
        if (repo.path / "setup.py").exists():
            with TemporaryDirectory() as tmp_dir:
                subprocess.check_call(
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
                    stdout=sys.stderr,
                )
                wheel = None
                for whl in Path(tmp_dir).glob("*.whl"):
                    if wheel is not None:
                        raise ValueError(
                            f"`pip wheel --no-deps {repo.path!s}` produced mutliple wheel files!"
                        )
                    wheel = whl
                if wheel is None:
                    raise ValueError(
                        f"`pip wheel --no-deps {repo.path!s}` did not produce a wheel file!"
                    )
                dist = JohnnyDist(str(wheel))
                # force JohnnyDist to read the dependencies before deleting the wheel:
                _ = dist.children
                return PipSourcePackage.from_dist(dist, repo.path)
        elif (repo.path / "requirements.txt").exists():
            # We just have a requirements.txt and no setup.py
            # Use the directory name as the package name
            name = repo.path.absolute().name
            if (repo.path / "VERSION").exists():
                with open(repo.path / "VERSION", "r") as f:
                    version = PipResolver.get_version(f.read().strip())
            else:
                version = PipResolver.get_version("0.0.0")
                log.info(f"Could not detect {repo.path} version. Using: {version}")
            return PipSourcePackage(
                name=name,
                version=version,
                dependencies=PipResolver.get_dependencies(repo.path),
                source_repo=repo,
                source="pip",
            )
        else:
            raise ValueError(f"{repo.path} neither has a setup.py nor a requirements.txt")
