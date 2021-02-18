from pathlib import Path
from tempfile import TemporaryDirectory
import subprocess
import sys
from typing import Iterable, Iterator, Optional

from johnnydep import JohnnyDist
from johnnydep.logs import configure_logging

from .dependencies import (
    Dependency, DependencyClassifier, DependencyResolver, DockerSetup, Package, SemanticVersion, SimpleSpec, Version
)


configure_logging(1)


class PipResolver(DependencyResolver):
    def __init__(self, package_spec_or_path: str, source: Optional[DependencyClassifier] = None):
        super().__init__(source=source)
        if (Path(package_spec_or_path) / "setup.py").exists():
            self.path: Optional[str] = package_spec_or_path
            self.package_spec: Optional[str] = None
        else:
            self.path = None
            self.package_spec = package_spec_or_path
        self._dist: Optional[JohnnyDist] = None

    @property
    def dist(self) -> JohnnyDist:
        if self._dist is None:
            if self.package_spec is not None:
                self._dist = JohnnyDist(self.package_spec)
            else:
                with TemporaryDirectory() as tmp_dir:
                    subprocess.check_call([
                        sys.executable, "-m", "pip", "wheel", "--no-deps", "-w", tmp_dir, self.path
                    ])
                    wheel = None
                    for whl in Path(tmp_dir).glob("*.whl"):
                        if wheel is not None:
                            raise ValueError(f"`pip wheel --no-deps {self.path}` produced mutliple wheel files!")
                        wheel = whl
                    if wheel is None:
                        raise ValueError(f"`pip wheel --no-deps {self.path}` did not produce a wheel file!")
                    self._dist = JohnnyDist(str(wheel))
                    # force JohnnyDist to read the dependencies before deleting the wheel:
                    _ = self._dist.children
            # add a package for the root dist
            self.resolve_dist(self._dist)
        return self._dist

    @staticmethod
    def _get_specifier(dist: JohnnyDist) -> SimpleSpec:
        try:
            return SimpleSpec(dist.specifier)
        except ValueError:
            return SimpleSpec("*")

    @staticmethod
    def _get_version(version_str: str, none_default: Optional[Version] = None) -> Optional[Version]:
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
        packages = []
        while queue:
            dist, sem_version = queue.pop()
            for version in sem_version.filter(
                    filter(
                        lambda v: v is not None,
                        (
                                PipResolver._get_version(v_str, none_default=Version.coerce(dist.version_installed))
                                for v_str in dist.versions_available
                        )
                    )
            ):
                if self.contains(dist.name, version):
                    continue
                package = Package(
                    name=dist.name,
                    version=version,
                    dependencies=[
                        Dependency(package=child.name, semantic_version=self._get_specifier(child))
                        for child in dist.children
                    ],
                    source="pip"
                )
                self.add(package)
                packages.append(package)
                if not recurse:
                    break
                queue.extend((child, self._get_specifier(child)) for child in dist.children)
        return packages

    def __iter__(self):
        if self._dist is None:
            _ = self.dist
        return super().__iter__()

    def __len__(self):
        if self._dist is None:
            _ = self.dist
        return super().__len__()

    def resolve_missing(self, dependency: Dependency) -> Iterator[Package]:
        return iter(self.resolve_dist(
            JohnnyDist(f"{dependency.package}{dependency.semantic_version}"), version=dependency.semantic_version)
        )


class PipClassifier(DependencyClassifier):
    name = "pip"
    description = "classifies the dependencies of Python packages using pip"

    def can_classify(self, path: str) -> bool:
        p = Path(path)
        return (p / "setup.py").exists() or (p / "requirements.txt").exists()

    def classify(self, path: str, resolvers: Iterable[DependencyResolver] = ()) -> DependencyResolver:
        return PipResolver(path, source=self)

    def docker_setup(self) -> Optional[DockerSetup]:
        return DockerSetup(
            apt_get_packages=("python3","python3-pip"),
            install_package_script="""#!/usr/bin/env bash
pip3 install $1==$2
""",
            load_package_script="""#!/usr/bin/env bash
python3 -c "import $1"
""",
            baseline_script="#!/usr/bin/env python3 -c \"\"\n"
        )
