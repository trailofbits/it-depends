from pathlib import Path
from typing import Iterator, Optional
import shutil
import subprocess
import logging
import re

from .dependencies import Version, SimpleSpec
from .dependencies import (
    Dependency, DependencyResolver, Package, PackageCache, ResolverAvailability, SourcePackage, SourceRepository
)
from .docker import DockerContainer, InMemoryDockerfile

logger = logging.getLogger(__name__)


_container: Optional[DockerContainer] = None

_UBUNTU_NAME_MATCH: re.Pattern = re.compile(r"^\s*name\s*=\s*\"ubuntu\"\s*$", flags=re.IGNORECASE)
_VERSION_ID_MATCH: re.Pattern = re.compile(r"^\s*version_id\s*=\s*\"([^\"]+)\"\s*$", flags=re.IGNORECASE)


def is_running_ubuntu(check_version: Optional[str] = None) -> bool:
    """
    Tests whether the current system is running Ubuntu

    If `check_version` is not None, the specific version of Ubuntu is also tested.
    """
    os_release_path = Path("/etc/os-release")
    if not os_release_path.exists():
        return False
    is_ubuntu = False
    version: Optional[str] = None
    with open(os_release_path, "r") as f:
        for line in f.readlines():
            line = line.strip()
            is_ubuntu = is_ubuntu or bool(_UBUNTU_NAME_MATCH.match(line))
            if check_version is None:
                if is_ubuntu:
                    return True
            elif version is None:
                m = _VERSION_ID_MATCH.match(line)
                if m:
                    version = m.group(1)
            else:
                break
    return is_ubuntu and (check_version is None or version == check_version)


def run_command(*args: str) -> bytes:
    """
    Runs the given command in Ubuntu 20.04

    If the host system is not runnign Ubuntu 20.04, the command is run in Docker.

    """
    if shutil.which(args[0]) is None or not is_running_ubuntu(check_version="20.04"):
        # we do not have apt installed natively or are not running Ubuntu
        global _container
        if _container is None:
            with InMemoryDockerfile("""FROM ubuntu:20.04

RUN apt-get update && apt-get install -y apt-file && apt-file update
""") as dockerfile:
                _container = DockerContainer("trailofbits/it-depends-apt", dockerfile=dockerfile)
                _container.rebuild()
        logger.debug(f"running {' '.join(args)} in Docker")
        p = _container.run(*args, interactive=False, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, rebuild=False)
    else:
        logger.debug(f"running {' '.join(args)}")
        p = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    if p.returncode != 0:
        raise subprocess.CalledProcessError(p.returncode, cmd=f"{' '.join(args)}")
    return p.stdout


class UbuntuResolver(DependencyResolver):
    name = "ubuntu"
    description = "expands dependencies based upon Ubuntu package dependencies"

    _pattern = re.compile(r" *(?P<package>[^ ]*)( *\((?P<version>.*)\))? *")
    _ubuntu_version = re.compile("([0-9]+:)*(?P<version>[^-]*)(-.*)*")

    def resolve(self, dependency: Dependency) -> Iterator[Package]:
        if dependency.source != "ubuntu":
            raise ValueError(f"{self} can not resolve dependencies from other sources ({dependency})")

        # Parses the dependencies of dependency.package out of the `apt show` command
        logger.info(f"Running apt-cache depends {dependency.package}")
        contents = run_command("apt", "show", dependency.package).decode("utf8")

        # Possibly means that the package does not appear ubuntu with the exact name
        if not contents:
            logger.info(f"Package {dependency.package} not found in ubuntu installed apt sources")
            return

        # Example depends line:
        # Depends: libc6 (>= 2.29), libgcc-s1 (>= 3.4), libstdc++6 (>= 9)
        version = None
        deps = []
        for line in contents.split("\n"):
            if line.startswith("Depends: "):
                for dep in line[9:].split(","):
                    matched = self._pattern.match(dep)
                    if not matched:
                        raise ValueError(f"Invalid dependency line in apt output for {dependency.package}: {line!r}")
                    dep_package = matched.group('package')
                    dep_version = matched.group('version')
                    try:
                        dep_version = dep_version.replace(" ", "")
                        SimpleSpec(dep_version.replace(" ", ""))
                    except Exception as e:
                        print ("UBUNTU DEP VERSION SPEC FAIL", dep_version)
                        dep_version = "*"  # Yolo FIXME Invalid simple block '= 1:7.0.1-12'

                    deps.append((dep_package, dep_version))
            if line.startswith("Version: "):
                version = line[9:]

        if version is None:
            logger.info(f"Package {dependency.package} not found in ubuntu installed apt sources")
            return

        matched = self._ubuntu_version.match(version)
        if not matched:
            logger.info(
                f"Failed to parse package {dependency.package} version: {version}")
            return
        version = Version.coerce(matched.group("version"))

        yield Package(name=dependency.package, version=version,
                      source=UbuntuResolver(),
                      dependencies=(
                          Dependency(package=pkg,
                                     semantic_version=SimpleSpec(ver),
                                     source=UbuntuResolver()
                                     )
                          for pkg, ver in deps
                      ))

    def __lt__(self, other):
        """Make sure that the Ubuntu Classifier runs last"""
        return False

    def is_available(self) -> ResolverAvailability:
        if not (shutil.which("apt") is not None and is_running_ubuntu()) and shutil.which("docker") is None:
            return ResolverAvailability(False,
                                          "`Ubuntu` classifier either needs to be running from Ubuntu 20.04 or "
                                          "to have Docker installed")
        return ResolverAvailability(True)

    def can_resolve_from_source(self, repo: SourceRepository) -> bool:
        return True

    def resolve_from_source(
            self, repo: SourceRepository, cache: Optional[PackageCache] = None
    ) -> Optional[SourcePackage]:
        return None
