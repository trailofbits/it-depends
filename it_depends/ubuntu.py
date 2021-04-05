from typing import Iterator, Optional

from .dependencies import (
    ClassifierAvailability, Dependency, DependencyClassifier, DependencyResolver, Package, PackageCache,
    SourceRepository
)


class UbuntuResolver(DependencyResolver):
    def resolve_missing(self, dependency: Dependency, for_package: Optional[Package] = None) -> Iterator[Package]:
        source = for_package.source
        if not (source == "native" or source == "ubuntu" or source == "cmake" or source == "autotools"):
            return
        print(f"TODO: Resolve {dependency.package} from {for_package.name}, source = {for_package.source.name}")
        # TODO: Match dependency.package against an Ubuntu package name and yield all of its dependencies from the
        #       ubuntu package DB
        yield from ()


class UbuntuClassifier(DependencyClassifier):
    name = "ubuntu"
    description = "expands dependencies based upon Ubuntu package dependencies"

    def __lt__(self, other):
        """Make sure that the Ubuntu Classifier runs last"""
        return False

    def is_available(self) -> ClassifierAvailability:
        # TODO: Check for docker if necessary later
        return ClassifierAvailability(True)

    def can_classify(self, repo: SourceRepository) -> bool:
        return True

    def classify(self, repo: SourceRepository, cache: Optional[PackageCache] = None):
        resolver = UbuntuResolver(self, cache)
        repo.resolvers.append(resolver)
        resolver.resolve_unsatisfied(repo)
