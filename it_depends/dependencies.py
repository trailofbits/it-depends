from multiprocessing import cpu_count
from tqdm import tqdm
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED

import functools
from abc import ABC, abstractmethod
from collections import defaultdict
from dataclasses import dataclass
import json
from pathlib import Path
from typing import (
    Dict, FrozenSet, Iterable, Iterator, List, Optional, Set, Union
)
import sys
from graphviz import Digraph
from semantic_version import SimpleSpec, Version
from semantic_version.base import BaseSpec as SemanticVersion
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
        self.name: str = name
        self.version: Version = version
        self.dependencies: FrozenSet[Dependency] = frozenset(dependencies)
        self.source = source

    def update_dependencies(self, dependencies: FrozenSet[Dependency]):
        self.dependencies = self.dependencies.union(dependencies)
        return self

    @property
    def resolver(self):
        """
        The initial main resolver for this package.
        Other resolvers could have added dependencies and perform modifications over it
        """
        return resolver_by_name(self.source)

    @classmethod
    def from_string(cls, description: str):
        """ A package selected by full name.
         For example:
                ubuntu:libc6@2.31
                ubuntu:libc6@2.31[]
                ubuntu:libc6@2.31[ubuntu:somepkg@<0.1.0]
                ubuntu:libc6@2.31[ubuntu:somepkg@<0.1.0, ubuntu:otherpkg@=2.1.0]
                ubuntu,native:libc6@2.31[ubuntu:somepkg@<0.1.0, ubuntu:otherpkg@=2.1.0, ubuntu:libc@*]

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
            dependencies = "[" + ",".join(sorted(map(str, self.dependencies))) + "]"
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

    def __hash__(self):
        return hash((self.version, self.name, self.version))


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
        """True if this particular dependency was set resolved"""
        raise NotImplementedError()

    @abstractmethod
    def set_resolved(self, dependency: Dependency):
        """True if this particular dependency as resolved"""
        raise NotImplementedError()

    @abstractmethod
    def set_updated(self, package: Package, resolver: str):
        """Update package for updates made by resolver"""
        raise NotImplementedError()

    @abstractmethod
    def was_updated(self, package: Package, resolver: str) -> bool:
        """True if package was updated by resolver"""
        raise NotImplementedError()

    @abstractmethod
    def updated_by(self, package: Package) -> FrozenSet[str]:
        """A set of resolver names that updated package"""
        raise NotImplementedError()

    @abstractmethod
    def package_versions(self, package_full_name: str) -> Iterator[Package]:
        """"""
        raise NotImplementedError()

    @abstractmethod
    def package_full_names(self) -> Iterator[str]:
        raise NotImplementedError()

    @abstractmethod
    def match(self, to_match: Dependency) -> Iterator[Package]:
        """
        Yields all packages in this collection of packages that match the Dependncy.

        This function does not perform any dependency resolution;
        it only matches against existing packages in this cache.

        """
        raise NotImplementedError()

    def get(self, source: Union[str, "DependencyResolver"], name: str, version: Union[str, Version]) -> Optional[Package]:
        pkg = Package(source=source, name=name, version=version)
        it = self.match(pkg.to_dependency())
        try:
            return next(it)
        except StopIteration:
            return None

    def to_obj(self):
        def package_to_dict(package):
            dependencies = { f"{dep.source}:{dep.package}":str(dep.semantic_version)  for dep in package.dependencies }
            ret = {
                "dependencies": dependencies,
                "source": package.source
            }
            if isinstance(package, SourcePackage):
                ret["is_source_package"] = True
            return ret


        return {
            package_full_name: {
                str(package.version): package_to_dict(package) for package in self.package_versions(package_full_name)
            }
            for package_full_name in self.package_full_names()
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
        """ List all unresolved dependencies of packages. """
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
        self._updated: Dict[Package, Set[str]] = defaultdict(set)  # source:package -> dep

    def __len__(self):
        return sum(sum(map(len, source.values())) for source in self._cache.values())

    def __iter__(self) -> Iterator[Package]:
        return (p for d in self._cache.values() for v in d.values() for p in v.values())

    def updated_by(self, package: Package) -> FrozenSet[str]:
        return frozenset(self._updated[package])

    def was_updated(self, package: Package, resolver:str) -> bool:
        return resolver in self._updated[package]

    def set_updated(self, package: Package, resolver: str):
        self._updated[package].add(resolver)

    def was_resolved(self, dependency: Dependency) -> bool:
        return dependency in self._resolved[f"{dependency.source}:{dependency.package}"]

    def set_resolved(self, dependency: Dependency):
        self._resolved[f"{dependency.source}:{dependency.package}"].add(dependency)

    def from_source(self, source: Union[str, "DependencyResolver"]) -> "PackageCache":
        if isinstance(source, DependencyResolver):
            source = source.name
        return InMemoryPackageCache({source: self._cache.setdefault(source, {})})

    def package_full_names(self) -> FrozenSet[str]:
        ret: Set[str] = set()
        for source, versions in self._cache.items():
            for name, version in versions.items():
                ret.add(f"{source}:{name}")
        return frozenset(ret)

    def package_versions(self, package_full_name: str) -> Iterator[Package]:
        package_source, package_name = package_full_name.split(":")
        packages = self._cache[package_source]
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
        original_package = self._cache.setdefault(package.source, {}).setdefault(package.name, {}).get(package.version)
        if original_package is not None:
            package = original_package.update_dependencies(package.dependencies)
        self._cache[package.source][package.name][package.version] = package

    def __str__(self):
        return '[' + ",".join(self.package_full_names()) + ']'


@functools.lru_cache()
def resolvers() -> FrozenSet["DependencyResolver"]:
    """ Collection of all the default instances of DependencyResolvers
    """
    return frozenset(cls() for cls in DependencyResolver.__subclasses__())


@functools.lru_cache()
def resolver_by_name(name: str) -> "DependencyResolver":
    """ Finds a resolver instance by name. The result is cached."""
    for instance in resolvers():
        if instance.name == name:
            return instance
    raise KeyError(name)


def is_known_resolver(name: str) -> bool:
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

    @abstractmethod
    def can_update_dependencies(self, package: Package) -> bool:
        return False

    @abstractmethod
    def update_dependencies(self, package: Package) -> Package:
        return package


class PackageRepository(InMemoryPackageCache):
    pass


def update_dependencies(package, repo, cache):
    # round two, fix point?
    print("UPDATE ", len(package.dependencies))
    print("Before:")
    print(f"  source_package dependencies: {len(package.dependencies)})")
    print(f"  source_package repo updated by: {repo.updated_by(package)}")
    print(f"  source_package cache updated by: {cache.updated_by(package)}")

    repo_package = repo.get(source=package.source, name=package.name, version=package.version)
    if repo_package:
        package = package.update_dependencies(repo_package.dependencies)
        print(f"Got a package from repo with {len(repo_package.dependencies)}")
    else:
        print("None package from repo")
    cache_package = cache.get(source=package.source, name=package.name, version=package.version)
    if cache_package:
        package = package.update_dependencies(cache_package.dependencies)
        print(f"Got a package from cache with {len(cache_package.dependencies)}")
    else:
        print("None package from cache")

    for resolver_updater in resolvers():
        if resolver_updater.can_update_dependencies(package):
            cache_was_updated = cache.was_updated(package, resolver_updater.name)
            repo_was_updated = repo.was_updated(package, resolver_updater.name)
            #If it was not previously updated at neither repo nor cache..
            if not repo_was_updated and not cache_was_updated:
                # run the resolver updater on this
                package = resolver_updater.update_dependencies(package)

            repo.set_updated(package, resolver_updater.name)
            cache.set_updated(package, resolver_updater.name)

            print(" UPDATE ", len(package.dependencies))
    print("After:")
    print(f"  source_package dependencies: {len(package.dependencies)})")
    print(f"  source_package repo updated by: {repo.updated_by(package)}")
    print(f"  source_package cache updated by: {cache.updated_by(package)}")

    repo.add(package)
    cache.add(package)
    print(f"  result {package}")
    return package


def resolve(
        repo_or_spec: Union[Package, Dependency, SourceRepository],
        cache: Optional[PackageCache] = None,
        depth_limit: int = -1,
        max_workers: Optional[int] = None,
        repo: Optional[PackageRepository] = None,
        queue: FrozenSet[Dependency] = frozenset(),
        t: Optional[tqdm] = None) -> PackageRepository:
    """
    Resolves the dependencies for a package, dependency, or source repository.

    If depth_limit is negative (the default), recursively resolve all dependencies.
    If depth_limit is greater than zero, only recursively resolve dependencies to that depth.

    """
    breakpoint()
    if t is None:
        with tqdm(desc="resolving unsatisfied", leave=False, unit=" deps", total=0) as t:
            return resolve(repo_or_spec=repo_or_spec, cache=cache, depth_limit=depth_limit, repo=repo, queue=queue, max_workers=max_workers, t=t)

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
                    print (resolver.name)
                    # repo_or_spec is a SourceRepository
                    source_package = resolver.resolve_from_source(repo_or_spec, cache=cache)
                    if source_package is None:
                        logger.debug(f"{resolver.name} can not resolve {repo_or_spec}")
                        print(f"{resolver.name} can not resolve {repo_or_spec}")
                        continue

                    # Found something in the source
                    # 2nd stage resolving (native)
                    # some resolvers could update the known dependencies of a package
                    # TODO: should keep updating until a fixed point
                    print ("Before:")
                    print (f"  source_package dependencies: {len(source_package.dependencies)})")
                    print (f"  source_package repo updated by: {repo.updated_by(source_package)}")
                    print (f"  source_package cache updated by: {cache.updated_by(source_package)}")
                    source_package = update_dependencies(source_package, repo=repo, cache=cache)
                    print ("After:")
                    print (f"  source_package dependencies: {len(source_package.dependencies)})")
                    print (f"  source_package repo updated by: {repo.updated_by(source_package)}")
                    print (f"  source_package repo updated by: {cache.updated_by(source_package)}")
                    repo.add(source_package)  # update the cached package
                    cache.add(source_package)  # update the cached package

            else:

                solutions = ()
                # check if dep is resolved in the cache
                if cache and cache.was_resolved(dep):
                    solutions = cache.match(dep)

                # check if the repo has the solution already
                elif not repo.was_resolved(dep):
                    solutions = dep.resolver.resolve(dep)

                print("EEEEEEEEEEEEEEXTENDING repo", solutions)
                repo.extend(solutions)
                repo.set_resolved(dep)
                t.update(len(repo))
                # cache the resolution
                if cache:
                    print ("EEEEEEEEEEEEEEXTENDING cache", solutions)
                    cache.extend(solutions)
                    cache.set_resolved(dep)

                print ("-"*80)
                print ("-"*80)
                print (f"Solving {str(dep)} with {tuple(map(str, solutions))}")
                print ("-"*80)
                print ("-"*80)

                for package in repo.match(dep):
                    update_dependencies(package, repo, cache)
                    # this package may be added/cached by previous resolution
                    # For example cargo over a source repo solves it all to the cache but
                    # none has native resolution done
                    cache.add(package)
                    repo.add(package)

            t.update(1)


            # If depth_limit was positive and have not reached 1
            # or it was set to something else keeps recurring
            if depth_limit != 1:
                unresolved_dependencies = repo.unresolved_dependencies()
                unresolved_dependencies = (x for x in unresolved_dependencies if x not in queue)
                unresolved_dependencies = tuple(unresolved_dependencies)

                if not unresolved_dependencies:
                    return repo

                print ("ADDING!!", len(unresolved_dependencies))
                t.total += len(unresolved_dependencies)

                for dep in sorted(unresolved_dependencies):
                    if cache is not None and cache.was_resolved(dep):
                        print (f"{str(dep)} was resolved in the cache")
                        repo.extend(cache.match(dep))
                        repo.set_resolved(dep)
                        t.update(1)
                    else:
                        resolve(repo_or_spec=dep, cache=cache, depth_limit=depth_limit-1, max_workers=max_workers, repo=repo, queue=queue.union(unresolved_dependencies), t=t)
            else:
                return repo

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



def resolveaa(
        repo_or_spec: Union[Package, Dependency, SourceRepository],
        cache: Optional[PackageCache] = None,
        depth_limit: int = -1,
        repo: Optional[PackageRepository] = None,
        queue: FrozenSet[Dependency] = frozenset(),
        max_workers: Optional[int] = None,
        pool: Optional[ThreadPoolExecutor] = None
) -> PackageRepository:
    """
    Resolves the dependencies for a package, dependency, or source repository.

    If depth_limit is negative (the default), recursively resolve all dependencies.
    If depth_limit is greater than zero, only recursively resolve dependencies to that depth.
    max_workers controls the number of spawned threads, if None cpu_count is used.
    """
    if depth_limit == 0:
        return

    if max_workers is None:
        try:
            max_workers = cpu_count()
        except NotImplementedError:
            max_workers = 5

    # just restart with an executor
    if pool is None and max_workers > 1:
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            resolve(repo_or_spec=repo_or_spec, cache=cache, depth_limit=depth_limit, repo=repo, queue=queue, max_workers=max_workers, pool=executor)

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
                # repo_or_spec is a SourceRepository
                source_package = None
                for resolver in resolvers():
                    if resolver.can_resolve_from_source(repo_or_spec):
                        source_package = resolver.resolve_from_source(repo_or_spec, cache=cache)
                        if source_package is None:
                            continue

                        # round two, fix point?
                        for resolver_updater in resolvers():
                            if resolver_updater.can_update_dependencies(source_package):
                                source_package = resolver_updater.update_dependencies(source_package)
                                print (repr(source_package))

                        repo.add(source_package)
                        cache.add(source_package) # update dependencies
                if source_package is None:
                    raise ValueError(f"Can not resolve {repo_or_spec}")
            else:
                # check if dep is resolved in the cache
                if cache and cache.was_resolved(dep):
                    solutions = cache.match(dep)
                    repo.extend(solutions)
                    repo.set_resolved(dep)

                # check if the repo has the solution already
                elif not repo.was_resolved(dep):
                    solutions = dep.resolver.resolve(dep)
                    repo.extend(solutions)
                    repo.set_resolved(dep)

                    if cache:
                        # cache the resolution
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

            # If depth_limit was positive and have not reached 1
            # or it was set to something else keeps recurring
            if depth_limit != 1:
                unresolved_dependencies = tuple(x for x in repo.unresolved_dependencies() if x not in queue)
                if not unresolved_dependencies:
                    return repo

                futures = {}
                for dep in sorted(unresolved_dependencies):
                    if cache is not None and cache.was_resolved(dep):
                        repo.extend(cache.match(dep))
                        repo.set_resolved(dep)
                    else:
                        futures = {pool.submit(resolve, dep, 0) for dep, package in resolution_cache.extend(packages) }
                        while futures:
                            done, futures = wait(futures, return_when=FIRST_COMPLETED)
                            for finished in done:
                                t.update(1)
                                depth, dep, new_packages = finished.result()


                        if cache is not None:
                            cache.set_resolved(dep)
                            cache.extend(new_packages)
                        packages.set_resolved(dep)
                        packages.extend(new_packages)
                        if depth_limit < 0 or depth < depth_limit:
                            futures |= {
                                executor.submit(self._resolve_worker, new_dep, depth)
                                for new_dep, package in resolution_cache.extend(new_packages)
                            }
                        resolve(repo_or_spec=dep, cache=cache, depth_limit=depth_limit-1, max_workers=max_workers, repo=repo, queue=queue.union(unresolved_dependencies), t=t)
            else:
                return repo

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
