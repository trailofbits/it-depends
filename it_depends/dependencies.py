from abc import ABC, abstractmethod
from collections import OrderedDict
import concurrent.futures
from dataclasses import dataclass
import json
from multiprocessing import cpu_count
from typing import (
    Dict, FrozenSet, Iterable, Iterator, List, Optional, OrderedDict as OrderedDictType, Set, Type, TypeVar, Union
)

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


class PackageCache(ABC):
    def open(self):
        pass

    def close(self):
        pass

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    @abstractmethod
    def __len__(self):
        raise NotImplementedError()

    @abstractmethod
    def __iter__(self) -> Iterator[Package]:
        raise NotImplementedError()

    def __contains__(self, package_spec: Union[str, Package, Dependency]):
        try:
            _ = next(iter(self.match(package_spec)))
            return True
        except StopIteration:
            return False

    @abstractmethod
    def from_source(self, source: Optional[str]) -> "PackageCache":
        raise NotImplementedError()

    @abstractmethod
    def package_versions(self, package_name: str) -> Iterator[Package]:
        raise NotImplementedError()

    @abstractmethod
    def package_names(self) -> FrozenSet[str]:
        raise NotImplementedError()

    @abstractmethod
    def match(self, to_match: Union[str, Package, Dependency]) -> Iterator[Package]:
        raise NotImplementedError()

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
                str(package.version): package_to_dict(package) for package in self.package_versions(package_name)
            }
            for package_name in self.package_names()
        }

    @abstractmethod
    def add(self, package: Package, source: Optional["DependencyClassifier"] = None):
        raise NotImplementedError()

    def extend(self, packages: Iterable[Package], source: Optional["DependencyClassifier"] = None):
        for package in packages:
            self.add(package, source=source)


class InMemoryPackageCache(PackageCache):
    def __init__(self, _cache: Optional[Dict[Optional[str], Dict[str, Dict[Version, Package]]]] = None):
        if _cache is None:
            self._cache: Dict[Optional[str], Dict[str, Dict[Version, Package]]] = {}
        else:
            self._cache = _cache

    def __len__(self):
        return sum(sum(map(len, source.values())) for source in self._cache.values())

    def __iter__(self) -> Iterator[Package]:
        return (p for d in self._cache.values() for v in d.values() for p in v.values())

    def from_source(self, source: Optional[str]) -> "InMemoryPackageCache":
        return InMemoryPackageCache({source: self._cache.setdefault(source, {})})

    def package_names(self) -> FrozenSet[str]:
        ret = set()
        for source_values in self._cache.values():
            ret |= source_values.keys()
        return frozenset(ret)

    def package_versions(self, package_name: str) -> Iterator[Package]:
        for packages in self._cache.values():
            if package_name in packages:
                yield from packages[package_name].values()

    def match(self, to_match: Union[str, Package, Dependency]) -> Iterator[Package]:
        if isinstance(to_match, Package):
            # Ignore the package source
            for source_dict in self._cache.values():
                package = source_dict.get(to_match.name, {}).get(to_match.version, None)
                if package is not None:
                    yield package
        elif isinstance(to_match, Dependency):
            for source_dict in self._cache.values():
                for version, package in source_dict.get(to_match.package, {}).items():
                    if version in to_match.semantic_version:
                        yield package
        else:
            return any(str(to_match) in source_dict for source_dict in self._cache.values())

    def add(self, package: Package, source: Optional["DependencyClassifier"] = None):
        if package in self:
            if max(len(p.dependencies) for p in self.match(package)) > len(package.dependencies):
                raise ValueError(f"Package {package!s} has already been resolved with more dependencies")
        if package.source is None and source is not None:
            package.source = source.name
        self._cache.setdefault(package.source, {}).setdefault(package.name, {})[package.version] = package


class DependencyResolver:
    def __init__(
            self,
            packages: Iterable[Package] = (),
            source: Optional["DependencyClassifier"] = None,
            cache: Optional[PackageCache] = None
    ):
        if cache is None:
            self._packages: PackageCache = InMemoryPackageCache()
        else:
            self._packages = cache
        self.source: Optional[DependencyClassifier] = source
        for package in packages:
            self.add(package)
        self._entries: int = 0
        self.resolved_dependencies: Dict[Dependency, Set[Package]] = {}

    @property
    def packages(self) -> PackageCache:
        return self._packages

    @packages.setter
    def packages(self, new_cache: PackageCache):
        if len(self._packages) > 0:
            # migrate the old cache to the new
            new_cache.extend(self, source=self.source)
        self._packages = new_cache

    def open(self):
        self.packages.open()

    def close(self):
        self.packages.close()

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
        return len(self.packages.from_source(self.source.name))

    def __iter__(self) -> Iterator[Package]:
        return iter(self.packages.from_source(self.source.name))

    def __contains__(self, package: Package):
        return package in self.packages

    def add(self, package: Package):
        self.packages.add(package, source=self.source)

    def extend(self, packages: Iterable[Package]):
        self.packages.extend(packages, source=self.source)

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
        matched = set(self.packages.match(dependency))
        yield from matched
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
        if isinstance(version, Version):
            return Package(package_name, version) in self.packages
        else:
            return Dependency(package_name, version) in self.packages


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


def resolve(path: str, cache: Optional[PackageCache] = None) -> PackageCache:
    if cache is None:
        cache = InMemoryPackageCache()
    resolvers: List[DependencyResolver] = []
    for classifier in CLASSIFIERS_BY_NAME.values():
        if classifier.is_available() and classifier.can_classify(path):
            with classifier.classify(path, resolvers) as resolver:
                resolver.packages = cache
                resolvers.append(resolver)
                for _ in resolver:
                    # some resolvers might be lazy and not actually resolve until they are iterated,
                    # so force the resolution so everything can be saved to the cache
                    pass
    return cache
