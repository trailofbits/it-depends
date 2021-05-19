from typing import Iterator, Optional
import shutil
import subprocess
import logging
import re
from .dependencies import Version, SimpleSpec
from .dependencies import (
    ClassifierAvailability, Dependency, DependencyClassifier, DependencyResolver, Package, PackageCache,
    SourceRepository
)

logger = logging.getLogger(__name__)


class UbuntuResolver(DependencyResolver):
    _pattern = re.compile(r" *(?P<package>[^ ]*)( *\((?P<version>.*)\))? *")
    _ubuntu_version = re.compile("([0-9]+:)*(?P<version>[^-]*)(-.*)*")

    def resolve_missing(self, dependency: Dependency) -> Iterator[Package]:
        source = dependency.source_name
        if not (source == "native" or source == "ubuntu" or source == "cmake" or source == "autotools"):
            return

        # Parses the dependencies of dependency.package out of the `apt show` command
        logger.info(f"Running apt-cache depends {dependency.package}")
        contents = subprocess.run(["apt", "show", dependency.package],
                                  stdout=subprocess.PIPE).stdout.decode("utf8")

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
                      source=UbuntuClassifier(),
                      dependencies=(
                          Dependency(package=pkg,
                                     semantic_version=SimpleSpec(ver),
                                     source=UbuntuClassifier()
                                     )
                          for pkg, ver in deps
                      ))


class UbuntuClassifier(DependencyClassifier):
    name = "ubuntu"
    description = "expands dependencies based upon Ubuntu package dependencies"

    def __lt__(self, other):
        """Make sure that the Ubuntu Classifier runs last"""
        return False

    def is_available(self) -> ClassifierAvailability:
        # TODO: Check for docker if necessary later
        if shutil.which("apt") is None:
            return ClassifierAvailability(False,
                                          "`Ubuntu` classifier needs apt-cache tool")

        return ClassifierAvailability(True)

    def can_classify(self, repo: SourceRepository) -> bool:
        return True

    def classify(self, repo: SourceRepository, cache: Optional[PackageCache] = None):
        resolver = UbuntuResolver(self, cache)
        repo.resolvers.append(resolver)
        resolver.resolve_unsatisfied(repo)
