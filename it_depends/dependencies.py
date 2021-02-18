from abc import ABC, abstractmethod
from collections import OrderedDict
import concurrent.futures
from dataclasses import dataclass
from itertools import chain
import json
from multiprocessing import cpu_count
from typing import Dict, Iterable, Iterator, List, Optional, OrderedDict as OrderedDictType, Set, Type, TypeVar, Union

from semantic_version import SimpleSpec, Version
from semantic_version.base import BaseSpec as SemanticVersion
from tqdm import tqdm


class Dependency:
    def __init__(self, package: str, semantic_version: SemanticVersion = SimpleSpec("*")):
        self.package: str = package
        self.semantic_version: SemanticVersion = semantic_version

    def __eq__(self, other):
        return isinstance(other, Dependency) and self.package == other.package and \
               self.semantic_version == other.semantic_version

    def __ne__(self, other):
        return not (self == other)

    def __hash__(self):
        return hash((self.package, self.semantic_version))


class Package:
    def __init__(
            self,
            name: str,
            version: Version,
            dependencies: Iterable[Dependency] = (),
            source: Optional[str] = None
    ):
        self.name: str = name
        self.version: Version = version
        self.dependencies: Dict[str, Dependency] = {
            dep.package: dep for dep in dependencies
        }
        self.source: Optional[str] = source

    def to_obj(self) -> Dict[str, Union[str, Dict[str, str]]]:
        ret = {
            "name": self.name,
            "version": str(self.version),
            "dependencies": {
                package: str(dep.semantic_version) for package, dep in self.dependencies.items()
            }
        }
        if self.source is not None:
            ret["source"] = self.source
        return ret

    def dumps(self) -> str:
        return json.dumps(self.to_obj())

    def __eq__(self, other):
        return isinstance(other, Package) and self.name == other.name and self.version == other.version

    def __ne__(self, other):
        return not (self == other)

    def __hash__(self):
        return hash((self.name, self.version))


class DependencyResolver:
    def __init__(self, packages: Iterable[Package] = (), source: Optional["DependencyClassifier"] = None):
        self.packages: Dict[str, Dict[Version, Package]] = {}
        self.source: Optional[DependencyClassifier] = source
        for package in packages:
            self.add(package)
        self._entries: int = 0
        self.resolved_dependencies: Dict[Dependency, Set[Package]] = {}

    def to_obj(self):
        def package_to_dict(package):
            ret = {
                "dependencies": {
                    package: str(dep.semantic_version) for package, dep in package.dependencies.items()
                }
            }
            if package.source is not None:
                ret["source"] = package.source
            return ret

        return {
            package_name: {
                str(version): package_to_dict(package) for version, package in versions.items()
            }
            for package_name, versions in self.packages.items()
        }

    def open(self):
        pass

    def close(self):
        pass

    def __enter__(self) -> "DependencyResolver":
        self._entries += 1
        if self._entries == 1:
            self.open()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self._entries -= 1
        if self._entries == 0:
            self.close()

    def __len__(self):
        return sum(map(len, self.packages.values()))

    def __iter__(self) -> Iterator[Package]:
        return chain(*(p.values() for p in self.packages.values()))

    def __contains__(self, package: Package):
        return package.name in self.packages and package.version in self.packages[package.name]

    def add(self, package: Package):
        if package in self:
            if len(self.packages[package.name][package.version].dependencies) > len(package.dependencies):
                raise ValueError(f"Package {package!s} has already been resolved with more dependencies")
        if package.source is None and self.source is not None:
            package.source = self.source.name
        self.packages.setdefault(package.name, {})[package.version] = package

    def extend(self, packages: Iterable[Package]):
        for package in packages:
            self.add(package)

    def resolve_missing(self, dependency: Dependency) -> Iterator[Package]:
        """
        Forces a resolution of a missing dependency

        This implementation simply yields nothing. Extending classes can extend this function to perform a custom
        resolution of the dependency. The dependency should not already have been resolved.
        """
        return iter(())

    def resolve(self, dependency: Dependency) -> Iterator[Package]:
        """Yields all previously resolved packages that satisfy the given dependency"""
        if dependency in self.resolved_dependencies:
            yield from iter(self.resolved_dependencies[dependency])
            return
        matched = set()
        for version, package in self.packages.get(dependency.package, {}).items():
            if version in dependency.semantic_version:
                matched.add(package)
                yield package
        if dependency not in self.resolved_dependencies:
            # we never tried to resolve this dependency before, so do a manual resolution
            for package in self.resolve_missing(dependency):
                if package not in matched:
                    matched.add(package)
                    self.add(package)
                    yield package
            self.resolved_dependencies[dependency] = matched

    def _resolve_unsatisfied(self, package: Package) -> List[Package]:
        ret = []
        for dep in package.dependencies.values():
            if dep in self.resolved_dependencies:
                continue
            ret.extend(self.resolve(dep))
        return ret

    def resolve_unsatisfied(self, max_workers: Optional[int] = None):
        """
        Resolves any packages dependencies that have not yet been resolved.

        This is expensive and may reproduce work. In general, it should only be called from subclasses with knowledge
        of specifically when it needs to be called.
        """
        if max_workers is None:
            try:
                max_workers = cpu_count()
            except NotImplementedError:
                max_workers = 5
        expanded_packages: Set[Package] = set(self)
        with tqdm(desc="resolving unsatisfied", leave=False, unit=" deps", total=len(expanded_packages)) as t:
            with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = {
                    executor.submit(self._resolve_unsatisfied, package) for package in expanded_packages
                }
                while futures:
                    done, futures = concurrent.futures.wait(futures, return_when=concurrent.futures.FIRST_COMPLETED)
                    for finished in done:
                        t.update(1)
                        for new_package in finished.result():
                            if new_package not in expanded_packages:
                                expanded_packages.add(new_package)
                                t.total += 1
                                futures.add(executor.submit(self._resolve_unsatisfied, new_package))

    def contains(self, package_name: str, version: Union[SemanticVersion, Version]):
        if package_name not in self.packages:
            return False
        elif isinstance(version, Version):
            return version in self.packages[package_name]
        else:
            for actual_version in self.packages[package_name].keys():
                if actual_version in version:
                    return True
            return False


CLASSIFIERS_BY_NAME: OrderedDictType[str, "DependencyClassifier"] = OrderedDict()


class ClassifierAvailability:
    def __init__(self, is_available: bool, reason: str = ""):
        if not is_available and not reason:
            raise ValueError("You must provide a reason if `not is_available`")
        self.is_available: bool = is_available
        self.reason: str = reason

    def __bool__(self):
        return self.is_available


@dataclass
class DockerSetup:
    apt_get_packages: List[str]
    install_package_script: str
    load_package_script: str
    baseline_script: str
    post_install: str = ""


C = TypeVar("C")


class DependencyClassifier(ABC):
    name: str
    description: str

    def __init_subclass__(cls, **kwargs):
        if not hasattr(cls, "name") or cls.name is None:
            raise TypeError(f"{cls.__name__} must define a `name` class member")
        elif not hasattr(cls, "description") or cls.description is None:
            raise TypeError(f"{cls.__name__} must define a `description` class member")
        global CLASSIFIERS_BY_NAME
        copy = CLASSIFIERS_BY_NAME.copy()
        copy[cls.name] = cls()
        CLASSIFIERS_BY_NAME.clear()
        for c in sorted(copy.values()):
            CLASSIFIERS_BY_NAME[c.name] = c

    @classmethod
    def default_instance(cls: Type[C]) -> C:
        return CLASSIFIERS_BY_NAME[cls.name]

    def docker_setup(self) -> Optional[DockerSetup]:
        return None

    def __lt__(self, other):
        if not isinstance(other, DependencyClassifier):
            return False
        if other.__class__.__lt__ is self.__class__.__lt__:
            return self.name < other.name
        else:
            # the other classifier has a custom implementation of __lt__ and we don't
            return not (other < self)

    def __eq__(self, other):
        return isinstance(other, DependencyClassifier) and self.name == other.name

    def is_available(self) -> ClassifierAvailability:
        return ClassifierAvailability(True)

    @abstractmethod
    def can_classify(self, path: str) -> bool:
        raise NotImplementedError()

    @abstractmethod
    def classify(self, path: str, resolvers: Iterable[DependencyResolver] = ()) -> DependencyResolver:
        raise NotImplementedError()


def resolve(path: str) -> DependencyResolver:
    package_list = DependencyResolver()
    resolvers: List[DependencyResolver] = []
    for classifier in CLASSIFIERS_BY_NAME.values():
        if classifier.is_available() and classifier.can_classify(path):
            with classifier.classify(path, resolvers) as resolver:
                package_list.extend(resolver)
                resolvers.append(resolver)
    return package_list
