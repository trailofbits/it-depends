import concurrent.futures
from dataclasses import dataclass
from multiprocessing import cpu_count
from pathlib import Path
import re
import shutil
from tempfile import NamedTemporaryFile
from typing import Iterator, Optional, Set

from tqdm import tqdm

from . import version as it_depends_version
from .docker import DockerContainer, InMemoryDockerfile, InMemoryFile
from .dependencies import (
    Dependency, DependencyResolver, DockerSetup, Package, PackageCache, resolvers, ResolverAvailability,
    SemanticVersion, SimpleSpec,
    SourcePackage, SourceRepository, Version
)


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


@dataclass(unsafe_hash=True, frozen=True, order=True)
class NativeLibrary:
    name: str
    path: str
    version: Optional[str]


class NativeResolver(DependencyResolver):
    name = "native"
    description = "attempts to detect native library usage by loading packages in a container"

    @staticmethod
    def get_libraries(
            container: DockerContainer, command: str, pre_command: Optional[str] = None
    ) -> Iterator[NativeLibrary]:
        """Yields all dynamic libraries loaded by `command`"""
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
                        yield NativeLibrary(name=m.group(4), path=m.group(3), version=m.group(6))
        finally:
            Path(stdout.name).unlink()

    @staticmethod
    def get_package_libraries(container: DockerContainer, package: Package) -> Iterator[NativeLibrary]:
        yield from NativeResolver.get_libraries(
            container=container,
            pre_command=f"./install.sh {package.name} {package.version!s}",
            command=f"./run.sh {package.name}"
        )

    @staticmethod
    def get_baseline_libraries(container: DockerContainer) -> Iterator[NativeLibrary]:
        yield from NativeResolver.get_libraries(
            container=container,
            command="./baseline.sh"
        )

    @staticmethod
    def _expand(package: Package, container: DockerContainer):
        return package, NativeResolver.get_package_libraries(container, package)

    def _native_dep(self, package: Package) -> Dependency:
        return Dependency(
            package=f"{package.name}",
            semantic_version=SemanticVersion.parse(str(package.version)),
            source=package.source
        )

    def expand(self, existing: PackageCache, max_workers: Optional[int] = None, cache: Optional[PackageCache] = None):
        sources: Set[DependencyResolver] = set()
        for package in existing:
            # Loop over all of the packages that have already been classified by other classifiers
            if package.source is not None and package.source not in sources \
                    and package.resolver.docker_setup() is not None:
                sources.add(package.resolver)
        if max_workers is None:
            max_workers = max(cpu_count() // 2, 1)
        for source in tqdm(sources, desc="resolving native libs", leave=False, unit=" sources"):
            with tqdm(desc=f"configuring Docker for {source.name}", leave=False, unit=" steps", total=4) as t:
                docker_setup = source.docker_setup()
                with make_dockerfile(docker_setup) as dockerfile:
                    t.update(1)
                    container = DockerContainer(
                        dockerfile, f"trailofbits/it-depends-{source.name!s}", tag=it_depends_version()
                    )
                    t.update(1)
                    container.rebuild()
                    t.update(1)
                    t.desc = f"running baseline for {source.name}"
                    baseline = set(NativeResolver.get_baseline_libraries(container))
                    t.update(1)
            packages = existing.from_source(source.name)
            with tqdm(desc=source.name, leave=False, unit=" deps", total=len(packages)) as t:
                with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
                    futures = set()
                    for package in packages:
                        native_dep = self._native_dep(package)
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
                                    if library.version is not None and library.version:
                                        try:
                                            version = Version.coerce(library.version)
                                            new_package = Package(
                                                name=library.name,
                                                version=version,
                                                source=self
                                            )
                                            existing.add(new_package)
                                        except ValueError:
                                            pass
                                    if library.name not in package.dependencies:
                                        try:
                                            required_version = SimpleSpec(f"~={library.version}")
                                        except ValueError:
                                            required_version = SimpleSpec("*")
                                        package.dependencies.add(Dependency(
                                            package=library.name,
                                            semantic_version=required_version,
                                            source=self
                                        ))
                                        # re-add the package so we can cache the new dependency
                                        existing.add(package)
                            if cache is not None:
                                cache.set_resolved(self._native_dep(package))

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
