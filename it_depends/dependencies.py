import functools
from abc import ABC, abstractmethod
from collections import defaultdict
import concurrent.futures
from dataclasses import dataclass
import json
from multiprocessing import cpu_count
from pathlib import Path
from typing import (
    Dict, FrozenSet, Iterable, Iterator, List, Optional, Set, Tuple, Union
)
import sys
from graphviz import Digraph
from semantic_version import SimpleSpec, Version
from semantic_version.base import BaseSpec as SemanticVersion
from tqdm import tqdm
from tempfile import mkdtemp
from shutil import rmtree
from subprocess import check_call
import atexit
import logging

logger = logging.getLogger(__name__)


class Dependency:
    def __init__(self, package: str, source: Union[str, "DependencyResolver"],
                 semantic_version: SemanticVersion = SimpleSpec("*")):
        assert(isinstance(semantic_version, SemanticVersion))
        if isinstance(source, DependencyResolver):
            source = source.name
        if not is_known_resolver(source):
            raise ValueError(f"{source} is not a known resolver")
        self.source: str = source
        self.package: str = package
        self.semantic_version: SemanticVersion = semantic_version

    @property
    def resolver(self):
        return resolver_by_name(self.source)

    @classmethod
    def from_string(cls, description):
        try:
            source, package_version = description.split(":")
            package, version_string = package_version.split("@")
            version = SimpleSpec(version_string)
        except Exception as e:
            raise ValueError(f"Can not parse dependency description <{description}>") from e
        return cls(source=source, package=package, semantic_version=version)

    def __str__(self):
        return f"{self.source}:{self.package}@{self.semantic_version!s}"

    def __eq__(self, other):
        return isinstance(other, Dependency) and \
                  self.package == other.package and \
                  self.source == other.source and \
                  self.semantic_version == other.semantic_version

    def __lt__(self, other):
        if not isinstance(other, Dependency):
            raise ValueError("Need a Dependency")
        return str(self) < str(other)

    def includes(self, other):
        if not isinstance(other, Dependency) or \
                  self.package != other.package and \
                  self.source != other.source:
            return False
        return self.semantic_version.clause.includes(other.semantic_version.clause)

    def __hash__(self):
        return hash((self.source, self.package, self.semantic_version))

    def match(self, package: "Package"):
        """True if package is a solution for this dependency"""
        return package.source == self.source and package.name == self.package and self.semantic_version.match(package.version)


class Package:
    def __init__(
            self,
            name: str,
            version: Union[str, Version],
            source: Union[str, "DependencyResolver"],
            dependencies: Iterable[Dependency] = (),
    ):
        if isinstance(version, str):
            version = Version(version)
        if isinstance(source, DependencyResolver):
            source = source.name
        if not is_known_resolver(source):
            raise ValueError(f"{source} is not a known resolver")
        self.name: str = name
        self.version: Version = version
        self.dependencies: FrozenSet[Dependency] = frozenset(dependencies)
        self.source: str = source

    @property
    def resolver(self):
        return resolver_by_name(self.source)

    @classmethod
    def from_string(cls, description: str):
        """ A package selected by full name.
         For example:
                ubuntu:libc6@2.31
                ubuntu:libc6@2.31[]
                ubuntu:libc6@2.31[ubuntu:somepkg@<0.1.0]
                ubuntu:libc6@2.31[ubuntu:somepkg@<0.1.0, ubuntu:otherpkg@=2.1.0]
        """
        source, tail = description.split(":", 1)
        name, version = tail.split("@", 1)
        dependencies: Iterable[Dependency] = ()
        if "[" in version:
            version, tail = version.split("[")
            tail = tail.strip(" ]")
            if tail:
                dependencies = map(Dependency.from_string, tail.split(","))

        return cls(name=name, version=Version(version), source=source, dependencies=dependencies)

    def __str__(self):
        if self.dependencies:
            # TODO(felipe) Strip dependency strings starting with self.source
            dependencies = "[" + ",".join(map(str, self.dependencies)) + "]"
        else:
            dependencies = ""
        return f"{self.source}:{self.name}@{self.version}" + dependencies

    def to_dependency(self) -> Dependency:
        return Dependency(
            package=self.name, semantic_version=self.resolver.parse_spec(f"={self.version}"), source=self.source
        )

    def to_obj(self):
        ret = {
            "source": self.source,
            "name": self.name,
            "version": str(self.version),
            "dependencies": {
                f"{dep.source}:{dep.package}": str(dep.semantic_version) for dep in self.dependencies
            }
        }
        return ret  # type: ignore

    def dumps(self) -> str:
        return json.dumps(self.to_obj())

    def __eq__(self, other):
        if isinstance(other, Package):
            return other.name == self.name and other.source == self.source and other.version == self.version
        return False


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
        """Returns the number of packages in this cache."""
        raise NotImplementedError()

    @abstractmethod
    def __iter__(self) -> Iterator[Package]:
        """Iterates over the packages in this cache."""
        raise NotImplementedError()

    def __contains__(self, pkg: Package):
        """True if pkg exists in this in this collection of packages."""
        for pkg_i in self:
            if pkg_i == pkg:
                return True
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
    def match(self, to_match: Dependency) -> Iterator[Package]:
        """
        Yields all packages in this collection of packages that match the Dependncy.

        This function does not perform any dependency resolution;
        it only matches against existing packages in this cache.

        """
        raise NotImplementedError()

    def to_obj(self):
        def package_to_dict(package):
            dependencies = {}
            for dep in package.dependencies:
                source = ""
                if dep.source != package.source:
                    source = f"{dep.source}:"
                dependencies[f"{source}{dep.package}"] = str(dep.semantic_version)

            ret = {
                "dependencies": dependencies,
                "source": package.source
            }
            if isinstance(package, SourcePackage):
                ret["is_source_package"] = True
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
            for dependency in package.dependencies:
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

    def unresolved_dependencies(self, packages: Optional[Iterable[Package]] = None) -> Iterable[Dependency]:
        """List all unresolved dependencies of packages."""
        unresolved = set()
        if packages is None:
            packages = self
        for package in packages:
            for dep in package.dependencies:
                if not self.was_resolved(dep) and dep not in unresolved:
                    unresolved.add(dep)
                    yield dep

class InMemoryPackageCache(PackageCache):
    def __init__(self, _cache: Optional[Dict[str, Dict[str, Dict[Version, Package]]]] = None):
        super().__init__()
        if _cache is None:
            self._cache: Dict[str, Dict[str, Dict[Version, Package]]] = {}
        else:
            self._cache = _cache
        self._resolved: Dict[str, Set[Dependency]] = defaultdict(set)  # source:package -> dep

    def __len__(self):
        return sum(sum(map(len, source.values())) for source in self._cache.values())

    def __iter__(self) -> Iterator[Package]:
        return (p for d in self._cache.values() for v in d.values() for p in v.values())

    def was_resolved(self, dependency: Dependency) -> bool:
        dependency_set = self._resolved[f"{dependency.source}:{dependency.package}"]
        if dependency in dependency_set:
            return True
        """
        # It could still be already resolved by composing a number of solver deps
        # But....
        hit = any(dep.includes(dependency) for dep in dependency_set)
        if hit:
            print (str(dependency), ", ".join(map(str, dependency_set)))
            print("HIT")
        """
        return False


    def set_resolved(self, dependency: Dependency):
        self._resolved[f"{dependency.source}:{dependency.package}"].add(dependency)

    def from_source(self, source: Union[str, "DependencyResolver"]) -> "PackageCache":
        if isinstance(source, DependencyResolver):
            source = source.name
        if not is_known_resolver(source):
            raise ValueError(f"{source} is not a known resolver")
        return InMemoryPackageCache({source: self._cache.setdefault(source, {})})

    def package_names(self) -> FrozenSet[str]:
        ret: Set[str] = set()
        for source_values in self._cache.values():
            ret |= source_values.keys()
        return frozenset(ret)

    def package_versions(self, package_name: str) -> Iterator[Package]:
        for packages in self._cache.values():
            if package_name in packages:
                yield from packages[package_name].values()

    def match(self, to_match: Union[Package, Dependency]) -> Iterator[Package]:
        if isinstance(to_match, Package):
            to_match = to_match.to_dependency()
        assert(isinstance(to_match, Dependency))
        source_dict = self._cache.get(to_match.source, {})
        for version, package in source_dict.get(to_match.package, {}).items():
            if to_match.semantic_version is not None and version in to_match.semantic_version:
                yield package

    def add(self, package: Package):
        if package in self:
            if max(len(p.dependencies) for p in self.match(package)) > len(package.dependencies):
                raise ValueError(f"Package {package!s} has already been resolved with more dependencies")
        self._cache.setdefault(package.source, {}).setdefault(package.name, {})[package.version] = package

    def __str__(self):
        return '[' + ",".join(self.package_names()) + ']'


class _ResolutionCache:
    def __init__(self, resolver: "DependencyResolver", results: PackageCache, cache: Optional[PackageCache] = None, t: Optional[tqdm] = None):
        self.resolver: DependencyResolver = resolver
        self.expanded_deps: Set[Dependency] = set()
        self.results: PackageCache = results
        self.existing_packages = set(results)
        self.cache: Optional[PackageCache] = cache
        self.t = t

    def extend(self, new_packages: Iterable[Package]) -> Set[Tuple[Dependency, Package]]:
        new_packages = set(new_packages)
        new_deps: Set[Tuple[Dependency, Package]] = set()
        while new_packages:
            pkg_list = new_packages
            new_packages = set()
            for package in pkg_list:
                for dep in package.dependencies:
                    if dep in self.expanded_deps:
                        continue
                    if self.cache is not None and self.cache.was_resolved(dep):
                        already_resolved = set(self.cache.match(dep))
                        cached = already_resolved - self.existing_packages
                        self.results.extend(cached)
                        if self.t:
                            self.t.total += len(cached)
                            self.t.update(len(cached))
                        new_packages |= cached
                        self.existing_packages |= cached
                    else:
                        new_deps.add((dep, package))
                        if self.t:
                            self.t.total += 1
                    self.expanded_deps.add(dep)
        return new_deps


@functools.lru_cache()
def resolvers():
    """ Collection of all the default instances of DependencyResolvers
    """
    return frozenset(cls() for cls in DependencyResolver.__subclasses__())


@functools.lru_cache()
def resolver_by_name(name: str):
    """ Finds a resolver instance by name. The result is cached."""
    for instance in resolvers():
        if instance.name == name:
            return instance
    raise KeyError(name)

def is_known_resolver(name: str):
    """Checks if name is a valid/known resolver name"""
    try:
        resolver_by_name(name)
        return True
    except KeyError:
        return False

class ResolverAvailability:
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


class SourceRepository:
    """represents a repo that we are analyzing from source"""
    def __init__(self, path: Union[Path, str]):
        super().__init__()
        if not isinstance(path, Path):
            path = Path(path)
        self.path: Path = path

    @staticmethod
    def from_git(git_url: str) -> "SourceRepository":
        tmpdir = mkdtemp()

        def cleanup():
            rmtree(tmpdir, ignore_errors=True)

        atexit.register(cleanup)

        check_call(["git", "clone", git_url], cwd=tmpdir)
        for file in Path(tmpdir).iterdir():
            if file.is_dir():
                return SourceRepository(file)
        raise ValueError(f"Error cloning {git_url}")

    @staticmethod
    def from_filesystem(path: Union[str, Path]) -> "SourceRepository":
        return SourceRepository(path)


class SourcePackage(Package):
    """A package extracted from source code rather than a package repository
    It is a package that exists on disk, but not necessarily in a remote repository.
    """

    def __init__(
            self,
            name: str,
            version: Version,
            source_repo: SourceRepository,
            source: str,
            dependencies: Iterable[Dependency] = (),
    ):
        super().__init__(name=name, version=version, dependencies=dependencies, source=source)
        self.source_repo: SourceRepository = source_repo

    def __str__(self):
        return f"{super().__str__()}:{self.source_repo.path.absolute()!s}"


class DependencyResolver:
    """Finds a set of Packages that agrees with a Dependency specification"""
    name: str
    description: str
    _instance = None

    def __new__(class_, *args, **kwargs):
        """A singleton (Only one default instance exists)"""
        if not isinstance(class_._instance, class_):
            class_._instance = super().__new__(class_, *args, **kwargs)
        return class_._instance

    def __init_subclass__(cls, **kwargs):
        if not hasattr(cls, "name") or cls.name is None:
            raise TypeError(f"{cls.__name__} must define a `name` class member")
        elif not hasattr(cls, "description") or cls.description is None:
            raise TypeError(f"{cls.__name__} must define a `description` class member")
        resolvers.cache_clear()

    @abstractmethod
    def resolve(self, dependency: Dependency) -> Iterator[Package]:
        """Yields all packages that satisfy the given dependency"""
        logger.info (f"{self} does not implement `resolve()`")
        raise NotImplementedError

    def _resolve_worker(self, dependency: Dependency, depth: int) -> Tuple[int, Dependency, List[Package]]:
        return depth + 1, dependency, list(self.resolve(dependency))

    @classmethod
    def parse_spec(cls, spec: str) -> SemanticVersion:
        """Parses a semantic version string into a semantic version object for this specific resolver"""
        return SimpleSpec.parse(spec)

    @classmethod
    def parse_version(cls, version_string: str) -> Version:
        """Parses a version string into a version object for this specific resolver"""
        return Version.coerce(version_string)

    def docker_setup(self) -> Optional[DockerSetup]:
        """Returns an optional docker setup for running this resolver"""
        return None

    def is_available(self) -> ResolverAvailability:
        return ResolverAvailability(True)

    @abstractmethod
    def can_resolve_from_source(self, repo: SourceRepository) -> bool:
        raise NotImplementedError()

    @abstractmethod
    def resolve_from_source(
            self, repo: SourceRepository, cache: Optional[PackageCache] = None
    ) -> Optional[SourcePackage]:
        """Resolves any new `SourcePackage`s in this repo"""
        raise NotImplementedError()


class PackageRepository(InMemoryPackageCache):
    pass


def resolve(
        repo_or_spec: Union[Package, Dependency, SourceRepository],
        cache: Optional[PackageCache] = None,
        depth_limit: int = -1,
        max_workers: Optional[int] = None,
        repo: Optional[PackageRepository] = None,
        queue: FrozenSet[Dependency] = frozenset()
) -> PackageRepository:
    """
    Resolves the dependencies for a package, dependency, or source repository.

    If depth_limit is negative (the default), recursively resolve all dependencies.
    If depth_limit is greater than zero, only recursively resolve dependencies to that depth.

    """
    if repo is None:
        repo = PackageRepository()
    if cache is None:
        cache = InMemoryPackageCache()  # Some resolvers may use it to save temporary results

    try:
        if isinstance(repo_or_spec, Dependency):
            dep: Optional[Dependency] = repo_or_spec
        elif isinstance(repo_or_spec, Package):
            dep = repo_or_spec.to_dependency()
        elif isinstance(repo_or_spec, SourceRepository):
            dep = None
        else:
            raise ValueError(f"repo_or_spec must be either a Package, Dependency, or SourceRepository")

        with cache:
            if dep is None:
                for resolver in resolvers():
                    if resolver.is_available():
                        # repo_or_spec is a SourceRepository
                        if resolver.can_resolve_from_source(repo_or_spec):
                            source_package = resolver.resolve_from_source(repo_or_spec, cache=cache)
                            if source_package is None:
                                continue
                            resolver_native = resolver_by_name("native")
                            native_deps = resolver_native.get_native_dependencies(source_package)
                            source_package.dependencies = source_package.dependencies.union(frozenset(native_deps))
                            repo.add(source_package)
                        else:
                            logger.debug(f"{resolver.name} can not resolve {repo_or_spec}")
            else:
                if cache and cache.was_resolved(dep):
                    repo.extend(cache.match(dep))
                    repo.set_resolved(dep)
                elif not repo.was_resolved(dep):
                    solutions = dep.resolver.resolve(dep)
                    repo.extend(solutions)
                    repo.set_resolved(dep)
                    if cache:
                        cache.extend(solutions)
                        cache.set_resolved(dep)


                for package in cache.match(dep):
                    # this package may be added/cached by previous resolution
                    # For example cargo over a source repo solves it all to the cache but
                    # none has native resolution done
                    resolver_native = resolver_by_name("native")
                    new_deps = resolver_native.get_native_dependencies(package)
                    new_dependencies = package.dependencies.union(frozenset(new_deps))
                    if package.dependencies != new_dependencies:
                        package.dependencies = new_dependencies
                        cache.add(package)
                        repo.add(package)
            while True:
                if depth_limit != 0:
                    unresolved_dependencies = tuple(x for x in repo.unresolved_dependencies() if x not in queue)
                    if not unresolved_dependencies:
                        return repo
                    for dep in sorted(unresolved_dependencies):
                        if cache is not None and cache.was_resolved(dep):
                            repo.extend(cache.match(dep))
                            repo.set_resolved(dep)
                        else:
                            resolve(repo_or_spec=dep, cache=cache, depth_limit=depth_limit-1, max_workers=max_workers, repo=repo, queue=queue.union(unresolved_dependencies))

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
