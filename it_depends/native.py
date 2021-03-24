import concurrent.futures
from dataclasses import dataclass
from multiprocessing import cpu_count
from pathlib import Path
import re
import shutil
from tempfile import NamedTemporaryFile
from typing import Iterable, Iterator, List, Optional

from tqdm import tqdm

from . import version as it_depends_version
from .docker import DockerContainer, InMemoryDockerfile, InMemoryFile
from .dependencies import (
    CLASSIFIERS_BY_NAME, ClassifierAvailability, Dependency, DependencyClassifier, DependencyResolver, DockerSetup,
    Package, PackageCache, SimpleSpec, Version
)


def make_dockerfile(docker_setup: DockerSetup) -> InMemoryDockerfile:
    install_script = InMemoryFile("install.sh", docker_setup.install_package_script.encode("utf-8"))
    run_script = InMemoryFile("run.sh", docker_setup.load_package_script.encode("utf-8"))
    baseline_script = InMemoryFile("baseline.sh", docker_setup.baseline_script.encode("utf-8"))
    return InMemoryDockerfile(f"""
FROM ubuntu:20.04

RUN mkdir -p /workdir

RUN ln -fs /usr/share/zoneinfo/America/New_York /etc/localtime

RUN DEBIAN_FRONTEND=noninteractive apt-get update && apt-get install -y --no-install-recommends strace {" ".join(docker_setup.apt_get_packages)}

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
    def __init__(self, resolvers: Iterable[DependencyResolver] = (), cache: Optional[PackageCache] = None):
        super().__init__(source=NativeClassifier.default_instance(), cache=cache)
        self.resolvers: List[DependencyResolver] = [
            r for r in resolvers if r.source is not None and r.source.docker_setup() is not None
        ]
        self._expanded: bool = False

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

    def expand(self, max_workers: Optional[int] = None):
        if self._expanded:
            return
        self._expanded = True
        if max_workers is None:
            max_workers = max(cpu_count() // 2, 1)
        for resolver in tqdm(self.resolvers, desc="resolving native libs", leave=False, unit=" sources"):
            with tqdm(desc=f"configuring Docker for {resolver.source.name}", leave=False, unit=" steps", total=4) as t:
                docker_setup = resolver.source.docker_setup()
                with make_dockerfile(docker_setup) as dockerfile:
                    t.update(1)
                    container = DockerContainer(
                        dockerfile, f"trailofbits/it-depends-{resolver.source.name!s}", tag=it_depends_version()
                    )
                    t.update(1)
                    container.rebuild()
                    t.update(1)
                    t.desc = f"running baseline for {resolver.source.name}"
                    baseline = set(NativeResolver.get_baseline_libraries(container))
                    t.update(1)
            with tqdm(desc=resolver.source.name, leave=False, unit=" deps", total=len(resolver)) as t:
                with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
                    futures = set()
                    for package in resolver:
                        cached = self.resolve_from_cache(package.to_dependency())
                        if cached is None:
                            futures.add(executor.submit(self._expand, package, container))
                        else:
                            t.update(1)
                            self.extend(cached)
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
                                                version=version
                                            )
                                            self.add(new_package)
                                        except ValueError:
                                            pass
                                    if library.name not in package.dependencies:
                                        try:
                                            required_version = SimpleSpec(f"~={library.version}")
                                        except ValueError:
                                            required_version = SimpleSpec("*")
                                        package.dependencies[library.name] = Dependency(
                                            package=library.name,
                                            semantic_version=required_version
                                        )
                                        # re-add the package so we can cache the new dependency
                                        self.add(package)
                            self.set_resolved_in_cache(package)

    def open(self):
        super().open()
        self.expand()

    def __len__(self):
        if not self._expanded:
            self.expand()
        return super().__len__()

    def __iter__(self):
        if not self._expanded:
            self.expand()
        return super().__iter__()


class NativeClassifier(DependencyClassifier):
    name = "native"
    description = "attempts to detect native library usage by loading packages in a container"

    def __lt__(self, other):
        return False

    def is_available(self) -> ClassifierAvailability:
        if shutil.which("docker") is None:
            return ClassifierAvailability(False, "`docker` does not appear to be installed! "
                                                 "Make sure it is installed and in the PATH.")
        return ClassifierAvailability(True)

    def can_classify(self, path: str) -> bool:
        for classifier in CLASSIFIERS_BY_NAME.values():
            if classifier.docker_setup() is not None and classifier.can_classify(path):
                return True
        return False

    def classify(
            self,
            path: str,
            resolvers: Iterable[DependencyResolver] = (),
            cache: Optional[PackageCache] = None
    ) -> NativeResolver:
        return NativeResolver(resolvers, cache=cache)
