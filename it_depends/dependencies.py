import re
import functools
from abc import ABC, abstractmethod
from collections import defaultdict
import concurrent.futures
from dataclasses import dataclass
import json
from multiprocessing import cpu_count
from pathlib import Path
from typing import (
    Dict, FrozenSet, Iterable, Iterator, List, Optional, Set, Tuple, TypeVar,
    Union
)
import sys
from graphviz import Digraph
from semantic_version import SimpleSpec, Version
from semantic_version.base import BaseSpec as SemanticVersion
from tqdm import tqdm
from contextlib import nullcontext


class Dependency:
    def __init__(self, package: str, source: Union[str, "DependencyClassifier"],
                 semantic_version: SemanticVersion = SimpleSpec("*")):
        self.package: str = package
        self.semantic_version: SemanticVersion = semantic_version
        # type forgiveness
        if isinstance(source, str):
            source_name = source
        else:
            source_name = source.name
        self.source_name: str = source_name

    @property
    def source(self):
        return classifier_by_name(self.source_name)

    def __eq__(self, other):
        if isinstance(other, Dependency):
            return self.source_name == other.source_name and \
                   self.package == other.package and \
                   self.semantic_version == other.semantic_version
        return False

    def __hash__(self):
        return hash((self.source_name, self.package, self.semantic_version))

    def __str__(self):
        return f"{self.source_name}:{self.package}@{self.semantic_version!s}"


class Package:
    def __init__(
            self,
            name: str,
            version: Version,
            source: Union[str, "DependencyClassifier"],
            dependencies: Iterable[Dependency] = (),
    ):
        self.name: str = name
        self.version: Version = version
        self.dependencies: Dict[str, Dependency] = {
            dep.package: dep for dep in dependencies
        }
        # type forgiveness
        if isinstance(source, str):
            source_name = source
        else:
            source_name = source.name
        self.source_name: str = source_name

    @property
    def source(self):
        return classifier_by_name(self.source_name)

    @classmethod
    def from_name(cls, fullname: str, dependencies: Iterable[Dependency] = ()):
        """ A package selected by full name.
         For example:
                ubuntu:libc6@2.31
        """
        source, name_version = fullname.split(":")
        name, version = name_version.split("@")
        return cls(name=name, version=version, source=source, dependencies=dependencies)

    def to_dependency(self) -> Dependency:
        return Dependency(package=self.name, semantic_version=SemanticVersion.parse(str(self.version)), source=self.source_name)

    def to_obj(self) -> Dict[str, Union[str, Dict[str, str]]]:
        ret = {
            "source": self.source_name,
            "name": self.name,
            "version": str(self.version),
            "dependencies": {
                package: str(dep.semantic_version) for package, dep in self.dependencies.items()
                }
        }
        return ret

    def dumps(self) -> str:
        return json.dumps(self.to_obj())

    def __eq__(self, other):
        return isinstance(other, Package) and\
               self.name == other.name and\
               self.source_name == other.source_name and\
               self.version == other.version

    def __hash__(self):
        return hash((self.source_name, self.name, self.version))

    def __str__(self):
        return f"{self.source_name}:{self.name}@{self.version}"


class PackageCache(ABC):
    """ An abstract base class for a collection of packages """
    def __init__(self):
        self._entries: int = 0

    def open(self):
        pass

    def close(self):
        pass

    def __enter__(self):
        if self._entries == 0:
            self.open()
        self._entries += 1
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self._entries -= 1
        if self._entries == 0:
            self.close()

    @abstractmethod
    def __len__(self):
        raise NotImplementedError()

    @abstractmethod
    def __iter__(self) -> Iterator[Package]:
        raise NotImplementedError()

    def __contains__(self, package_spec: Union[str, Package, Dependency]):
        if isinstance(package_spec, Dependency):
            return self.was_resolved(package_spec)
        try:
            next(iter(self.match(package_spec)))
            return True
        except StopIteration:
            return False

    @abstractmethod
    def was_resolved(self, dependency: Dependency) -> bool:
        raise NotImplementedError()

    @abstractmethod
    def set_resolved(self, dependency: Dependency):
        raise NotImplementedError()

    @abstractmethod
    def from_source(self, source_name: str) -> "PackageCache":
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
            if isinstance(package, SourcePackage):
                ret["is_source_package"] = True
            if package.source is not None:
                ret["source"] = package.source.name
            return ret

        return {
            package_name: {
                str(package.version): package_to_dict(package) for package in self.package_versions(package_name)
            }
            for package_name in self.package_names()
        }

    @property
    def source_packages(self) -> Set["SourcePackage"]:
        return {package for package in self if isinstance(package, SourcePackage)}

    def to_dot(self, sources: Optional[Iterable[Package]] = None) -> Digraph:
        """Renders a Graphviz Dot graph of the dependency hierarchy.

        If sources is not None, only render the graph rooted at the sources.

        If sources is None and there is at least one SourcePackage in the cache, render the graph using that
        SourcePackage as a root.

        """
        if sources is None:
            return self.to_dot(self.source_packages)
        sources = list(sources)
        if not sources:
            sources = list(self)
            dot = Digraph()
        else:
            dot = Digraph(comment=f"Dependencies for {', '.join(map(str, sources))}")
        package_ids: Dict[Package, str] = {}
        dependency_ids: Dict[Dependency, str] = {}

        def add_package(pkg: Package) -> str:
            if pkg not in package_ids:
                pkg_id = f"package{len(package_ids)}"
                package_ids[pkg] = pkg_id
                dot.node(pkg_id, label=str(pkg), shape="rectangle")
                return pkg_id
            else:
                return package_ids[pkg]

        def add_dependency(dep: Dependency) -> str:
            if dep not in dependency_ids:
                dep_id = f"dep{len(dependency_ids)}"
                dependency_ids[dep] = dep_id
                dot.node(dep_id, label=str(dep), shape="oval")
                return dep_id
            else:
                return dependency_ids[dep]

        while sources:
            package = sources.pop()
            pid = add_package(package)
            for dependency in package.dependencies.values():
                already_expanded = dependency in dependency_ids
                did = add_dependency(dependency)
                dot.edge(pid, did)
                if not already_expanded:
                    for satisfied_dep in self.match(dependency):
                        already_expanded = satisfied_dep in package_ids
                        spid = add_package(satisfied_dep)
                        dot.edge(did, spid)
                        if not already_expanded:
                            sources.append(satisfied_dep)
        return dot

    @abstractmethod
    def add(self, package: Package):
        raise NotImplementedError()

    def extend(self, packages: Iterable[Package]):
        for package in packages:
            self.add(package)


class InMemoryPackageCache(PackageCache):
    def __init__(self, _cache: Optional[Dict[str, Dict[str, Dict[Version, Package]]]] = None):
        super().__init__()
        if _cache is None:
            self._cache: Dict[str, Dict[str, Dict[Version, Package]]] = {}
        else:
            self._cache = _cache
        self._resolved: Dict[str, Set[Dependency]] = defaultdict(set)

    def __len__(self):
        return sum(sum(map(len, source.values())) for source in self._cache.values())

    def __iter__(self) -> Iterator[Package]:
        return (p for d in self._cache.values() for v in d.values() for p in v.values())

    def was_resolved(self, dependency: Dependency) -> bool:
        return dependency in self._resolved[dependency.source_name]

    def set_resolved(self, dependency: Dependency):
        self._resolved[dependency.source_name].add(dependency)

    def from_source(self, source_name: str) -> "PackageCache":
        return InMemoryPackageCache({source_name: self._cache.setdefault(source_name, {})})

    def package_names(self) -> FrozenSet[str]:
        ret: Set[str] = set()
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
                    if to_match.semantic_version is not None and version in to_match.semantic_version:
                        yield package
        else:
            return any(str(to_match) in source_dict for source_dict in self._cache.values())

    def add(self, package: Package):
        if package in self:
            if max(len(p.dependencies) for p in self.match(package)) > len(package.dependencies):
                raise ValueError(f"Package {package!s} has already been resolved with more dependencies")
        source_name = package.source_name
        self._cache.setdefault(source_name, {}).setdefault(package.name, {})[package.version] = package


class _ResolutionCache:
    def __init__(self, resolver: "DependencyResolver", results: PackageCache):
        self.resolver: DependencyResolver = resolver
        self.expanded_deps: Set[Dependency] = set()
        self.results: PackageCache = results
        self.existing_packages = set(results)

    def extend(self, new_packages: Iterable[Package], t: tqdm) -> Set[Tuple[Dependency, Package]]:
        new_packages = set(new_packages)
        new_deps: Set[Tuple[Dependency, Package]] = set()
        while new_packages:
            pkg_list = new_packages
            new_packages = set()
            for package in pkg_list:
                for dep in package.dependencies.values():
                    if dep in self.expanded_deps:
                        continue
                    already_resolved = self.resolver.resolve_from_cache(dep)
                    if already_resolved is None:
                        new_deps.add((dep, package))
                        t.total += 1
                    else:
                        cached = set(already_resolved) - self.existing_packages
                        self.results.extend(cached)
                        t.total += len(cached)
                        t.update(len(cached))
                        new_packages |= cached
                        self.existing_packages |= cached
                    self.expanded_deps.add(dep)
        return new_deps


@functools.lru_cache()
def resolvers():
    """ Collection of all the default instances of DependencyResolvers
    """
    return tuple(cls() for cls in DependencyResolver.__subclasses__())


@functools.lru_cache()
def resolver_by_name(name: str):
    """ Finds a resolver instance by name. The result is cached."""
    for instance in resolvers():
        if instance.name == name:
            return instance
    raise KeyError(name)

class DependencyResolver:
    """  Finds a set of Packages that agrees with a Dependency specification
    """
    name: str
    description: str
    _instance = None

    def __new__(class_, *args, **kwargs):
        """ A singleton (Only one default instance exists) """
        if not isinstance(class_._instance, class_):
            class_._instance = super().__new__(class_, *args, **kwargs)
        return class_._instance

    def __init_subclass__(cls, **kwargs):
        if not hasattr(cls, "name") or cls.name is None:
            raise TypeError(f"{cls.__name__} must define a `name` class member")
        elif not hasattr(cls, "description") or cls.description is None:
            raise TypeError(f"{cls.__name__} must define a `description` class member")
        resolvers.cache_clear()


    def __init__(self, cache: Optional[PackageCache] = None):
        if cache is None:
            self._cache: PackageCache = InMemoryPackageCache()
        else:
            self._cache = cache
        self._entries: int = 0

    def open(self):
        self._cache.__enter__()

    def close(self):
        self._cache.__exit__(None, None, None)

    def __enter__(self) -> "DependencyResolver":
        self._entries += 1
        if self._entries == 1:
            self.open()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self._entries -= 1
        if self._entries == 0:
            self.close()

    def resolve_missing(self, dependency: Dependency) -> Iterator[Package]:
        """
        Forces a resolution of a missing dependency

        This is called automatically from `resolve(...)`, if necessary.

        This implementation simply yields nothing. Extending classes can extend this function to perform a custom
        resolution of the dependency. The dependency should not already have been resolved.

        Calling this function alone will not add the resulting packages to the cache.
        """
        return iter(())

    def resolve_from_cache(self, dependency: Dependency) -> Optional[Iterator[Package]]:
        """Returns an iterator over all of the packages that satisfy the given dependency, or `None` if the dependency
        has not yet been resolved.

        """
        if self._cache.was_resolved(dependency):
            return self._cache.match(dependency)
        else:
            return None

    def set_resolved_in_cache(self, dependency_or_package: Union[Dependency, Package]):
        if isinstance(dependency_or_package, Package):
            self._cache.set_resolved(dependency_or_package.to_dependency())
        else:
            self._cache.set_resolved(dependency_or_package)

    def cache(self, package: Package):
        self._cache.add(package)

    def resolve(
            self, dependency: Dependency, record_results: bool = True, check_cache: bool = True,

    ) -> Iterator[Package]:
        """Yields all packages that satisfy the given dependency, resolving the dependency if necessary

        If the dependency is resolved, it is added to the cache
        """
        if record_results and not check_cache:
            raise ValueError("`check_cache` may only be False if `record_results` is also False")
        elif check_cache and self._cache.was_resolved(dependency):
            yield from self._cache.match(dependency)
            return
        # we never tried to resolve this dependency before, so do a manual resolution
        for package in self.resolve_missing(dependency):
            if record_results:
                self.cache(package)
            yield package
        if record_results:
            self._cache.set_resolved(dependency)

    def _resolve_worker(self, dependency: Dependency) -> Tuple[Dependency, List[Package]]:
        return dependency, list(self.resolve(dependency, record_results=False, check_cache=False))

    def resolve_unsatisfied(self, packages: PackageCache, max_workers: Optional[int] = None):
        """
        Resolves any packages dependencies that have not yet been resolved, saving them to the cache.

        This is expensive and may reproduce work. In general, it should only be called from subclasses with knowledge
        of specifically when it needs to be called.
        """
        if max_workers is None:
            try:
                max_workers = cpu_count()
            except NotImplementedError:
                max_workers = 5

        with tqdm(desc="resolving unsatisfied", leave=False, unit=" deps", total=0) as t:
            resolution_cache = _ResolutionCache(self, results=packages)
            with concurrent.futures.ThreadPoolExecutor(
                    max_workers=max_workers) as executor:
                futures = {
                    executor.submit(self._resolve_worker, dep)
                    for dep, package in resolution_cache.extend(packages, t)
                }
                while futures:
                    done, futures = concurrent.futures.wait(futures, return_when=concurrent.futures.FIRST_COMPLETED)
                    for finished in done:
                        t.update(1)
                        dep, new_packages = finished.result()
                        self._cache.set_resolved(dep)
                        self._cache.extend(new_packages)
                        packages.set_resolved(dep)
                        packages.extend(new_packages)
                        futures |= {
                            executor.submit(self._resolve_worker, new_dep)
                            for new_dep, package in resolution_cache.extend(new_packages, t)
                        }


class SourcePackage(Package):
    """A package extracted from source code rather than a package repository
    It is a package that exists on disk, but not necessarily in a remote repository.
    """

    def __init__(
            self,
            name: str,
            version: Version,
            source_path: Path,
            source: Union[str, "DependencyClassifier"],
            dependencies: Iterable[Dependency] = (),
    ):
        super().__init__(name=name, version=version, dependencies=dependencies, source=source)
        self.source_path: Path = source_path

    def __str__(self):
        return f"{super().__str__()}:{self.source_path.absolute()!s}"


class PackageRepository(InMemoryPackageCache):
    pass

class SourceRepository(PackageRepository):
    """represents a repo that we are analyzing from source"""
    def __init__(
            self,
            path: Union[Path, str],
            packages: Iterable[SourcePackage] = (),
    ):
        super().__init__()
        if not isinstance(path, Path):
            path = Path(path)
        self.path: Path = path
        self._packages: Set[SourcePackage] = set(packages)
        self.extend(self._packages)

    @property
    def source_packages(self) -> Set[SourcePackage]:
        return self._packages

    def add(self, package: Package):
        if isinstance(package, SourcePackage):
            self._packages.add(package)
        super().add(package=package)

class SpecRepository(PackageRepository):
    """A repository of packages that we are analyzing starting from a package specification"""
    def __init__(
            self,
            package_spec: str,
            packages: Iterable[SourcePackage] = (),
    ):
        super().__init__()
        self.package_spec: str = package_spec
        self.extend(packages)

    @property
    def source_packages(self) -> Set[SourcePackage]:
        return self._packages

    def add(self, package: Package):
        if isinstance(package, SourcePackage):
            self._packages.add(package)
        super().add(package=package)


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


@functools.lru_cache()
def classifiers():
    """ Sorted collection of all the default instances of DependencyClassifiers
    The result of this function is cached so the sorting is done only once.
    Though if a new subclass of DependencyClassifier is created the cache is
    recreated. Note that previous DependencyClassifier instances will not change.
    """
    return tuple(sorted(cls() for cls in DependencyClassifier.__subclasses__()))


@functools.lru_cache()
def classifier_by_name(name: str):
    """ Finds a classifier instance by name. The result is cached."""
    for instance in classifiers():
        if instance.name == name:
            return instance
    raise KeyError(name)


class DependencyClassifier(ABC):
    name: str
    description: str
    _instance = None

    def __new__(class_, *args, **kwargs):
        """ A singleton (Only one default instance exists) """
        if not isinstance(class_._instance, class_):
            class_._instance = super().__new__(class_, *args, **kwargs)
        return class_._instance

    def __init_subclass__(cls, **kwargs):
        if not hasattr(cls, "name") or cls.name is None:
            raise TypeError(f"{cls.__name__} must define a `name` class member")
        elif not hasattr(cls, "description") or cls.description is None:
            raise TypeError(f"{cls.__name__} must define a `description` class member")
        classifiers.cache_clear()

    @classmethod
    def parse_spec(cls, spec: str) -> SemanticVersion:
        return SimpleSpec.parse(spec)

    @classmethod
    def parse_version(cls, version_string: str) -> Version:
        return Version.coerce(version_string)

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

    def __hash__(self):
        return hash(self.name)

    def is_available(self) -> ClassifierAvailability:
        return ClassifierAvailability(True)

    @abstractmethod
    def can_classify(self, repo: SourceRepository) -> bool:
        raise NotImplementedError()

    @abstractmethod
    def classify(self, repo: SourceRepository, cache: Optional[PackageCache] = None):
        """Resolves any new `SourcePackage`s in this repo, as well as their dependencies"""
        raise NotImplementedError()


class UnusedClassifier(DependencyClassifier):
    name: str = "unknown"
    description: str = "Used for testing"

    def is_available(self) -> ClassifierAvailability:
        return ClassifierAvailability(False, "Unused classifier")

    def can_classify(self, repo: SourceRepository) -> bool:
        return False

    def classify(self, repo: SourceRepository, cache: Optional[PackageCache] = None):
        raise NotImplementedError()


def resolve(path_or_spec: Union[str, Path], cache: Optional[PackageCache] = None) -> PackageRepository:
    if re.match(r"[^:/]+:[^@/]+@.*", path_or_spec):
        repo = SpecRepository(path_or_spec)

        for resolver in DependencyResolver.__subclasses__():
            breakpoint()
            resolver(cache=cache)
    else:
        repo = SourceRepository(path_or_spec)
    try:
        if cache is None:
            cm = nullcontext()
        else:
            cm = cache
        with cm:
            for classifier in classifiers():
                if classifier.is_available() and classifier.can_classify(repo):
                    classifier.classify(repo, cache=cache)
    except KeyboardInterrupt:
        if sys.stderr.isatty() and sys.stdin.isatty():
            try:
                while True:
                    sys.stderr.write("Would you like to output the partial results? [Yn] ")
                    choice = input().lower()
                    if choice == "" or choice == "y":
                        return repo
                    elif choice == "n":
                        sys.exit(1)
            except KeyboardInterrupt:
                sys.exit(1)
        raise
    return repo
