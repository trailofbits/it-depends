import concurrent.futures
import functools
from dataclasses import dataclass
from logging import getLogger
from multiprocessing import cpu_count
from pathlib import Path
import re
import shutil
import subprocess
from tempfile import NamedTemporaryFile
from typing import Iterator, Optional, Set, Tuple

from tqdm import tqdm

from . import version as it_depends_version
from .apt import file_to_package
from .docker import DockerContainer, InMemoryDockerfile, InMemoryFile
from .dependencies import (
    Dependency, DependencyResolver, DockerSetup, Package, PackageCache, resolvers, ResolverAvailability,
    SemanticVersion, SimpleSpec,
    SourcePackage, SourceRepository, Version
)

logger = getLogger(__file__)


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
                            yield NativeLibrary(name=name, path=path, version=SemanticVersion.parse('*'))
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
    def configure_docker(
            source: DependencyResolver, run_baseline: bool = True
    ) -> Tuple[DockerContainer, Set[Dependency]]:
        docker_setup = source.docker_setup()
        if docker_setup is None:
            raise ValueError(f"source {source.name} does not support native dependency resolution")
        with tqdm(desc=f"configuring Docker for {source.name}", leave=False, unit=" steps", total=3, initial=1) as t:
            with make_dockerfile(docker_setup) as dockerfile:
                container = DockerContainer(f"trailofbits/it-depends-{source.name!s}", dockerfile,
                                            tag=it_depends_version())
                t.update(1)
                container.rebuild()
                t.update(1)
                if run_baseline:
                    t.desc = f"running baseline for {source.name}"
                    return container, set(NativeResolver.get_baseline_dependencies(container))
                else:
                    return container, set()

    @staticmethod
    def get_native_dependencies(package: Package, use_baseline: bool = False) -> Iterator[Dependency]:
        """Yields the native dependencies for an individual package"""
        if not package.resolver.docker_setup():
            return
        container, baseline = NativeResolver.configure_docker(package.resolver, run_baseline=use_baseline)
        for dep in NativeResolver.get_package_dependencies(container, package):
            if dep not in baseline:
                yield dep

    def expand(self, existing: PackageCache, max_workers: Optional[int] = None, use_baseline: bool = False,
               cache: Optional[PackageCache] = None):
        """Resolves the native dependencies for all packages in the cache"""
        sources: Set[DependencyResolver] = set()
        for package in existing:
            # Loop over all of the packages that have already been classified by other classifiers
            if package.source is not None and package.source not in sources \
                    and package.resolver.docker_setup() is not None:
                sources.add(package.resolver)
        if max_workers is None:
            max_workers = max(cpu_count() // 2, 1)
        for source in tqdm(sources, desc="resolving native libs", leave=False, unit=" sources"):
            container, baseline = NativeResolver.configure_docker(source=source, run_baseline=use_baseline)
            packages = existing.from_source(source.name)
            with tqdm(desc=source.name, leave=False, unit=" deps", total=len(packages)) as t:
                with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
                    futures = set()
                    for package in packages:
                        native_dep = package.to_dependency()
                        if cache is not None and cache.was_resolved(native_dep):
                            t.update(1)
                            existing.extend(cache.match(native_dep))
                        else:
                            futures.add(executor.submit(self._expand, package, container))
                    while futures:
                        done, futures = concurrent.futures.wait(futures, return_when=concurrent.futures.FIRST_COMPLETED)
                        for finished in done:
                            t.update(1)
                            package, libraries = finished.result()
                            for library in libraries:
                                if library not in baseline:
                                    # ignore the library version, because native library requirements aren't
                                    # typically versioned
                                    if all(dep.package != library.name for dep in package.dependencies):
                                        package.dependencies += library.to_dependency()
                                        # re-add the package so we can cache the new dependency
                                        existing.add(package)
                            if cache is not None:
                                cache.set_resolved(package.to_dependency())

    def __lt__(self, other):
        """Make sure that the Native Classifier runs second-to-last, before the Ubuntu Classifier"""
        return other.name == "ubuntu"

    def is_available(self) -> ResolverAvailability:
        if shutil.which("docker") is None:
            return ResolverAvailability(False, "`docker` does not appear to be installed! "
                                               "Make sure it is installed and in the PATH.")
        return ResolverAvailability(True)

    def can_resolve_from_source(self, repo: SourceRepository) -> bool:
        for resolver in resolvers():  # type: ignore
            if resolver.docker_setup() is not None and resolver.can_resolve_from_source(repo):
                return True
        return False

    def resolve_from_source(
            self, repo: SourceRepository, cache: Optional[PackageCache] = None
    ) -> Optional[SourcePackage]:
        return None
