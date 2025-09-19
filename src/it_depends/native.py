"""Native dependency resolution using Docker containers."""

from __future__ import annotations

import re
from logging import getLogger
from pathlib import Path
from tempfile import NamedTemporaryFile
from threading import Lock
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator

from tqdm import tqdm

from . import version as it_depends_version
from .dependencies import (
    Dependency,
    DependencyResolver,
    DockerSetup,
    Package,
    SemanticVersion,
)
from .docker import DockerContainer, InMemoryDockerfile, InMemoryFile

logger = getLogger(__name__)


def make_dockerfile(docker_setup: DockerSetup) -> InMemoryDockerfile:
    """Create a Dockerfile from Docker setup configuration."""
    install_script = InMemoryFile("install.sh", docker_setup.install_package_script.encode("utf-8"))
    run_script = InMemoryFile("run.sh", docker_setup.load_package_script.encode("utf-8"))
    baseline_script = InMemoryFile("baseline.sh", docker_setup.baseline_script.encode("utf-8"))
    pkgs = " ".join(docker_setup.apt_get_packages)
    return InMemoryDockerfile(
        f"""
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
""",
        local_files=(install_script, run_script, baseline_script),
    )


STRACE_LIBRARY_REGEX = re.compile(r"^open(at)?\(\s*[^,]*\s*,\s*\"((.+?)([^\./]+)\.so(\.(.+?))?)\".*")
CONTAINERS_BY_SOURCE: dict[DependencyResolver, DockerContainer] = {}
BASELINES_BY_SOURCE: dict[DependencyResolver, frozenset[Dependency]] = {}
_CONTAINER_LOCK: Lock = Lock()


def get_dependencies(container: DockerContainer, command: str, pre_command: str | None = None) -> Iterator[Dependency]:
    """Yield all dynamic libraries loaded by `command`, in order, including duplicates."""
    with NamedTemporaryFile(prefix="stdout", delete=False) as stdout:
        pre_command = f"{pre_command} > /dev/null 2>/dev/null && " if pre_command is not None else ""
        command = f"{pre_command}strace -e open,openat -f {command} 3>&1 1>&2 2>&3"
        try:
            container.run(
                "bash",
                "-c",
                command,
                rebuild=False,
                interactive=False,
                stdout=stdout,
                check_existence=False,
            )
            with Path(stdout.name).open() as f:
                for line in f:
                    m = STRACE_LIBRARY_REGEX.match(line)
                    if m:
                        path = m.group(2)
                        if path not in ("/etc/ld.so.cache",) and path.startswith("/"):
                            yield Dependency(
                                package=path,
                                source="ubuntu",  # make the package be from the UbuntuResolver
                                semantic_version=SemanticVersion.parse("*"),
                            )
        finally:
            Path(stdout.name).unlink()


def get_package_dependencies(container: DockerContainer, package: Package) -> Iterator[Dependency]:
    """Get dependencies for a specific package."""
    yield from get_dependencies(
        container=container,
        pre_command=f"./install.sh {package.name} {package.version!s}",
        command=f"./run.sh {package.name}",
    )


def get_baseline_dependencies(container: DockerContainer) -> Iterator[Dependency]:
    """Get baseline dependencies for a container."""
    yield from get_dependencies(container=container, command="./baseline.sh")


def container_for(source: DependencyResolver) -> DockerContainer:
    """Get or create Docker container for a dependency resolver."""
    with _CONTAINER_LOCK:
        if source in CONTAINERS_BY_SOURCE:
            return CONTAINERS_BY_SOURCE[source]
        docker_setup = source.docker_setup()
        if docker_setup is None:
            msg = f"source {source.name} does not support native dependency resolution"
            raise ValueError(msg)
        with (
            tqdm(
                desc=f"configuring Docker for {source.name}",
                leave=False,
                unit=" steps",
                total=2,
                initial=1,
            ) as t,
            make_dockerfile(docker_setup) as dockerfile,
        ):
            container = DockerContainer(
                f"trailofbits/it-depends-{source.name!s}",
                dockerfile,
                tag=it_depends_version(),
            )
            t.update(1)
            container.rebuild()
            CONTAINERS_BY_SOURCE[source] = container
            return container


def baseline_for(source: DependencyResolver) -> frozenset[Dependency]:
    """Get baseline dependencies for a source."""
    with _CONTAINER_LOCK:
        if source not in BASELINES_BY_SOURCE:
            baseline = frozenset(get_baseline_dependencies(container_for(source)))
            BASELINES_BY_SOURCE[source] = baseline
            return baseline
        return BASELINES_BY_SOURCE[source]


def get_native_dependencies(package: Package, *, use_baseline: bool = False) -> Iterator[Dependency]:
    """Yield the native dependencies for an individual package."""
    if not package.resolver.docker_setup():
        return
    container = container_for(package.resolver)
    baseline = baseline_for(package.resolver) if use_baseline else frozenset()
    for dep in get_package_dependencies(container, package):
        if dep not in baseline:
            yield dep
