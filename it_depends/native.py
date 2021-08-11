from logging import getLogger
from pathlib import Path
import re
import shutil
import subprocess
from tempfile import NamedTemporaryFile
from threading import Lock
from typing import Dict, FrozenSet, Iterator, Optional, Tuple

from tqdm import tqdm

from . import version as it_depends_version
from .apt import file_to_package
from .docker import DockerContainer, InMemoryDockerfile, InMemoryFile
from .dependencies import (
    Dependency, DependencyResolver, DockerSetup, Package, PackageCache, resolvers, ResolverAvailability,
    SemanticVersion, SourcePackage, SourceRepository
)

logger = getLogger(__name__)


def make_dockerfile(docker_setup: DockerSetup) -> InMemoryDockerfile:
    install_script = InMemoryFile("install.sh", docker_setup.install_package_script.encode("utf-8"))
    run_script = InMemoryFile("run.sh", docker_setup.load_package_script.encode("utf-8"))
    baseline_script = InMemoryFile("baseline.sh", docker_setup.baseline_script.encode("utf-8"))
    pkgs = " ".join(docker_setup.apt_get_packages)
    return InMemoryDockerfile(f"""
FROM ubuntu:20.04

RUN mkdir -p /workdir

RUN ln -fs /usr/share/zoneinfo/America/New_York /etc/localtime

RUN DEBIAN_FRONTEND=noninteractive apt-get update && apt-get install -y --no-install-recommends strace {pkgs}

{docker_setup.post_install}

WORKDIR /workdir

COPY install.sh .
COPY run.sh .
COPY baseline.sh .
RUN chmod +x *.sh
""", local_files=(install_script, run_script, baseline_script))


STRACE_LIBRARY_REGEX = re.compile(r"^open(at)?\(\s*[^,]*\s*,\s*\"((.+?)([^\./]+)\.so(\.(.+?))?)\".*")
CONTAINERS_BY_SOURCE: Dict[DependencyResolver, DockerContainer] = {}
BASELINES_BY_SOURCE: Dict[DependencyResolver, FrozenSet[Dependency]] = {}
_CONTAINER_LOCK: Lock = Lock()


class NativeResolver(DependencyResolver):
    name = "native"
    description = "attempts to detect native library usage by loading packages in a container"

    @staticmethod
    def get_dependencies(
            container: DockerContainer, command: str, pre_command: Optional[str] = None
    ) -> Iterator[Dependency]:
        """Yields all dynamic libraries loaded by `command`, in order, including duplicates"""
        stdout = NamedTemporaryFile(prefix="stdout", delete=False)
        if pre_command is not None:
            pre_command = f"{pre_command} > /dev/null 2>/dev/null && "
        else:
            pre_command = ""
        command = f"{pre_command}strace -e open,openat -f {command} 3>&1 1>&2 2>&3"
        try:
            container.run("bash", "-c", command, rebuild=False, interactive=False, stdout=stdout, check_existence=False)
            stdout.close()
            with open(stdout.name, "r") as f:
                for line in f.readlines():
                    m = STRACE_LIBRARY_REGEX.match(line)
                    if m:
                        path = m.group(2)
                        if path not in ("/etc/ld.so.cache",):
                            name = m.group(4)
                            try:
                                # see if this path matches to an Ubuntu library:
                                name = file_to_package(path)
                            except (ValueError, subprocess.CalledProcessError):
                                pass
                            yield Dependency(package=name, source="ubuntu", semantic_version=SemanticVersion.parse('*'))
        finally:
            Path(stdout.name).unlink()

    @staticmethod
    def get_package_dependencies(container: DockerContainer, package: Package) -> Iterator[Dependency]:
        yield from NativeResolver.get_dependencies(
            container=container,
            pre_command=f"./install.sh {package.name} {package.version!s}",
            command=f"./run.sh {package.name}"
        )

    @staticmethod
    def get_baseline_dependencies(container: DockerContainer) -> Iterator[Dependency]:
        yield from NativeResolver.get_dependencies(
            container=container,
            command="./baseline.sh"
        )

    @staticmethod
    def _expand(package: Package, container: DockerContainer):
        return package, NativeResolver.get_package_dependencies(container, package)

    @staticmethod
    def container_for(source: DependencyResolver) -> DockerContainer:
        with _CONTAINER_LOCK:
            if source in CONTAINERS_BY_SOURCE:
                return CONTAINERS_BY_SOURCE[source]
            docker_setup = source.docker_setup()
            if docker_setup is None:
                raise ValueError(f"source {source.name} does not support native dependency resolution")
            with tqdm(
                    desc=f"configuring Docker for {source.name}", leave=False, unit=" steps", total=2, initial=1
            ) as t, make_dockerfile(docker_setup) as dockerfile:
                container = DockerContainer(f"trailofbits/it-depends-{source.name!s}", dockerfile,
                                            tag=it_depends_version())
                t.update(1)
                container.rebuild()
                CONTAINERS_BY_SOURCE[source] = container
                return container

    @staticmethod
    def baseline_for(source: DependencyResolver) -> FrozenSet[Dependency]:
        with _CONTAINER_LOCK:
            if source not in BASELINES_BY_SOURCE:
                baseline = frozenset(NativeResolver.get_baseline_dependencies(NativeResolver.container_for(source)))
                BASELINES_BY_SOURCE[source] = baseline
                return baseline
            else:
                return BASELINES_BY_SOURCE[source]

    @staticmethod
    def configure_docker(
            source: DependencyResolver, run_baseline: bool = True
    ) -> Tuple[DockerContainer, FrozenSet[Dependency]]:
        if run_baseline:
            baseline = NativeResolver.baseline_for(source)
        else:
            baseline = frozenset()
        return NativeResolver.container_for(source), baseline

    @staticmethod
    def get_native_dependencies(package: Package, use_baseline: bool = False) -> Iterator[Dependency]:
        """Yields the native dependencies for an individual package"""
        if not package.resolver.docker_setup():
            return
        container, baseline = NativeResolver.configure_docker(package.resolver, run_baseline=use_baseline)
        for dep in NativeResolver.get_package_dependencies(container, package):
            if dep not in baseline:
                yield dep

    def resolve(self, dependency: Dependency) -> Iterator[Package]:
        raise StopIteration()

    def __lt__(self, other):
        """Make sure that the Native Classifier runs second-to-last, before the Ubuntu Classifier"""
        return other.name == "ubuntu"

    def is_available(self) -> ResolverAvailability:
        if shutil.which("docker") is None:
            return ResolverAvailability(False, "`docker` does not appear to be installed! "
                                               "Make sure it is installed and in the PATH.")
        return ResolverAvailability(True)

    def can_resolve_from_source(self, repo: SourceRepository) -> bool:
        return False

    def resolve_from_source(
            self, repo: SourceRepository, cache: Optional[PackageCache] = None
    ) -> Optional[SourcePackage]:
        return None

    def can_update_dependencies(self, package: Package) -> bool:
        return self.name not in package.source

    def update_dependencies(self, package: Package) -> Package:
        """ Update the dependencies in package """
        native_deps = self.get_native_dependencies(package)
        package.dependencies = package.dependencies.union(frozenset(native_deps))
        return package
