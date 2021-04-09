from typing import Iterator, Optional
import shutil
import subprocess
import logging
import re
from .dependencies import Version, SimpleSpec
logger = logging.getLogger(__name__)

from .dependencies import (
    ClassifierAvailability, Dependency, DependencyClassifier, DependencyResolver, Package, PackageCache,
    SourceRepository
)


class UbuntuResolver(DependencyResolver):
    _pattern = re.compile(r" *(?P<package>[^ ]*)( *\((?P<version>.*)\))? *")

    def resolve_missing(self, dependency: Dependency, for_package: Optional[Package] = None) -> Iterator[Package]:
        source = for_package.source.name
        if not (source == "native" or source == "ubuntu" or source == "cmake" or source == "autotools"):
            return
        # TODO: Match dependency.package against an Ubuntu package name and yield all of its dependencies from the
        #       ubuntu package DB
        logger.info(f"Running apt-cache depends {dependency.package}")
        contents = subprocess.run(["apt", "show", dependency.package],
                                  stdout=subprocess.PIPE).stdout.decode("utf8")

        if not contents:
            logger.info(f"Package {dependency.package} not found in ubuntu installed apt sources")
            return

        version = None
        deps = []
        for line in contents.split("\n"):
            if line.startswith("Depends: "):
                for dep in line[9:].split(","):
                    matched = self._pattern.match(dep)
                    dep_package = matched.group('package')
                    dep_version = matched.group('version')
                    dep_version = "*" # Yolo FIXME
                    deps.append((dep_package, dep_version))
            if line.startswith("Version: "):
                version = line[9:].split(":")[-1]
                version = "0.0.0"  # Yolo FIXME

        if version is None:
            logger.info(f"Package {dependency.package} not found in ubuntu installed apt sources")
            return


        version = Version.coerce(version)
        yield Package(name=dependency.package, version=version,
                      source=UbuntuClassifier.default_instance(),
                      dependencies=(
                          Dependency(package=pkg,
                                     semantic_version=SimpleSpec(ver))
                          for pkg, ver in deps
                                   )
                    )



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
