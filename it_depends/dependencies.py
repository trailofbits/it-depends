import atexit
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
import functools
from abc import ABC, abstractmethod
from collections import defaultdict
from dataclasses import dataclass
import json
import logging
from multiprocessing import cpu_count
from pathlib import Path
from shutil import rmtree
from subprocess import check_call
import sys
from tempfile import mkdtemp
from typing import (
    Any,
    Dict,
    FrozenSet,
    Iterable,
    Iterator,
    List,
    Optional,
    Set,
    Tuple,
    Union,
)

from graphviz import Digraph
from semantic_version import SimpleSpec, Version
from semantic_version.base import BaseSpec as SemanticVersion
from tqdm import tqdm

from .graphs import RootedDiGraph

logger = logging.getLogger(__name__)


class Vulnerability:
    """Represents a specific vulnerability"""

    def __init__(self, id: str, aliases: Iterable[str], summary: str) -> None:
        self.id = id
        self.aliases = list(aliases)
        self.summary = summary

    def to_compact_str(self) -> str:
        return f"{self.id} ({', '.join(self.aliases)})"

    def to_obj(self) -> Dict[str, Union[str, List[str]]]:
        return {"id": self.id, "aliases": self.aliases, "summary": self.summary}

    def __eq__(self, other):
        if issubclass(other.__class__, Vulnerability):
            return self.id == other.id
        return False

    def __hash__(self):
        return hash((self.id, "".join(self.aliases), self.summary))

    def __lt__(self, other):
        if not issubclass(other.__class__, Vulnerability):
            raise ValueError("Need a Vulnerability")
        return self.id < other.id


class Dependency:
    def __init__(
        self,
        package: str,
        source: Union[str, "DependencyResolver"],
        semantic_version: SemanticVersion = SimpleSpec("*"),
    ):
        assert isinstance(semantic_version, SemanticVersion)
        if isinstance(source, DependencyResolver):
            source = source.name
        if not is_known_resolver(source):
            raise ValueError(f"{source} is not a known resolver")
        self.source: str = source
        self.package: str = package
        self.semantic_version: SemanticVersion = semantic_version

    @property
    def package_full_name(self) -> str:
        return f"{self.source}:{self.package}"

    @property
    def resolver(self) -> "DependencyResolver":
        return resolver_by_name(self.source)

    @classmethod
    def from_string(cls, description):
        try:
            source, tail = description.split(":", 1)
            package, *remainder = tail.split("@", 1)
            version_string = "@".join(remainder)
            if version_string:
                resolver = resolver_by_name(source)
                version = resolver.parse_spec(version_string)
            else:
                version = SimpleSpec("*")
        except Exception as e:
            raise ValueError(f"Can not parse dependency description <{description}>") from e
        return cls(source=source, package=package, semantic_version=version)

    def __str__(self):
        return f"{self.source}:{self.package}@{self.semantic_version!s}"

    def __eq__(self, other):
        return (
            isinstance(other, Dependency)
            and self.package == other.package
            and self.source == other.source
            and self.semantic_version == other.semantic_version
        )

    def __lt__(self, other):
        if not isinstance(other, Dependency):
            raise ValueError("Need a Dependency")
        return str(self) < str(other)

    def includes(self, other):
        if (
            not isinstance(other, Dependency)
            or self.package != other.package
            and self.source != other.source
        ):
            return False
        return self.semantic_version.clause.includes(other.semantic_version.clause)

    def __hash__(self):
        return hash((self.source, self.package, self.semantic_version))

    def match(self, package: "Package"):
        """True if package is a solution for this dependency"""
        return (
            package.source == self.source
            and package.name == self.package
            and self.semantic_version.match(package.version)
        )


class AliasedDependency(Dependency):
    """An Aliased Dependency represents a dependency that has been aliased in a project.

    For instance, NPM allows this to have multiple version of the same dependency in your
    dependency chain.
    """
    def __init__(self,
                 package: str,
                 alias_name: str,
                 source: Union[str, "DependencyResolver"],
                 semantic_version: SemanticVersion = SimpleSpec("*"),
                 ):
        self.alias_name = alias_name
        super().__init__(package, source, semantic_version)

    def __eq__(self, other: Any) -> bool:
        """Checks equality."""
        return isinstance(other, AliasedDependency) and self.alias_name == other.alias_name and super().__eq__(other)

    def __hash__(self) -> int:
        """Hash computation."""
        return hash((self.alias_name, super().__hash__()))

    def __str__(self) -> str:
        """String representation."""
        return f"{self.source}:{self.alias_name}@{self.package}@{self.semantic_version!s}"


class Package:
    def __init__(
        self,
        name: str,
        version: Union[str, Version],
        source: Union[str, "DependencyResolver"],
        dependencies: Iterable[Dependency] = (),
        vulnerabilities: Iterable[Vulnerability] = (),
    ):
        if isinstance(version, str):
            version = Version(version)
        self.name: str = name
        self.version: Version = version
        self.dependencies: FrozenSet[Dependency] = frozenset(dependencies)
        if isinstance(source, DependencyResolver):
            self.source: str = source.name
        else:
            self.source = source
        self.vulnerabilities: FrozenSet[Vulnerability] = frozenset(vulnerabilities)

    @property
    def full_name(self) -> str:
        return f"{self.source}:{self.name}"

    def update_dependencies(self, dependencies: FrozenSet[Dependency]):
        self.dependencies = self.dependencies.union(dependencies)
        return self

    def update_vulnerabilities(self, vulnerabilities: FrozenSet[Vulnerability]):
        self.vulnerabilities = self.vulnerabilities.union(vulnerabilities)
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
        """A package selected by full name.
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

        return cls(
            name=name,
            version=Version(version),
            source=source,
            dependencies=dependencies,
        )

    def __str__(self):
        if self.dependencies:
            # TODO(felipe) Strip dependency strings starting with self.source
            dependencies = "[" + ",".join(map(str, sorted(self.dependencies))) + "]"
        else:
            dependencies = ""
        return f"{self.source}:{self.name}@{self.version}" + dependencies

    def to_dependency(self) -> Dependency:
        return Dependency(
            package=self.name,
            semantic_version=self.resolver.parse_spec(f"={self.version}"),
            source=self.source,
        )

    def to_obj(self):
        ret = {
            "source": self.source,
            "name": self.name,
            "version": str(self.version),
            "dependencies": {
                f"{dep.source}:{dep.package}": str(dep.semantic_version)
                for dep in self.dependencies
            },
            "vulnerabilities": [vuln.to_obj() for vuln in self.vulnerabilities],
        }
        return ret  # type: ignore

    def dumps(self) -> str:
        return json.dumps(self.to_obj())

    def __eq__(self, other):
        if isinstance(other, Package):
            return (
                other.name == self.name
                and other.source == self.source
                and other.version == self.version
            )
        return False

    def __lt__(self, other):
        return isinstance(other, Package) and (self.name, self.source, self.version) < (
            other.name,
            other.source,
            other.version,
        )

    def __hash__(self):
        return hash((self.version, self.name, self.version))


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

    def __repr__(self):
        return f"{self.__class__.__name__}({str(self.path)!r})"

    def __str__(self):
        return str(self.path)


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
        vulnerabilities: Iterable[Vulnerability] = (),
    ):
        super().__init__(
            name=name,
            version=version,
            dependencies=dependencies,
            source=source,
            vulnerabilities=vulnerabilities,
        )
        self.source_repo: SourceRepository = source_repo

    def __str__(self):
        return f"{super().__str__()}:{self.source_repo.path.absolute()!s}"


class DependencyGraph(RootedDiGraph[Package, SourcePackage]):
    root_type = SourcePackage
    _collapsed: bool = False

    @property
    def source_packages(self) -> Set[SourcePackage]:
        return self.roots

    def packages_by_name(self) -> Dict[Tuple[str, str], Set[Package]]:
        ret: Dict[Tuple[str, str], Set[Package]] = {}
        for node in self:
            name = node.source, node.name
            if name not in ret:
                ret[name] = {node}
            else:
                ret[name].add(node)
        return ret

    def collapse_versions(self) -> "DependencyGraph":
        """
        Group all versions of a package into a single node.
        All dependency edges will be grouped into a single edge with a wildcard semantic version.

        """
        if self._collapsed:
            return self
        graph = DependencyGraph()
        package_instances = self.packages_by_name()
        packages_by_name: Dict[str, Package] = {}
        # choose the maximum version among all packages of the same name:
        for (package_source, package_name), instances in package_instances.items():
            # convert all of the dependencies to SimpleSpec("*") wildcard versions:
            deps = {
                Dependency(package=dep.package, source=dep.source)
                for instance in instances
                for dep in instance.dependencies
            }
            if len(instances) == 1:
                pkg = next(iter(instances))
            else:
                source_packages_in_instances = self.source_packages & instances
                version = max(p.version for p in instances)
                if source_packages_in_instances:
                    # at least one of the instances is a source package, so make the collapsed package a source package
                    source_repos = {s.source_repo for s in source_packages_in_instances}
                    source_repo = next(iter(source_repos))
                    if len(source_repos) > 1:
                        logger.warning(
                            f"package {package_source}:{package_name} is provided by multiple source "
                            f"repositories: {', '.join(map(str, source_repos))}. "
                            f"Collapsing to {source_repo}."
                        )
                    pkg = SourcePackage(
                        name=package_name,
                        version=version,
                        source_repo=source_repo,
                        source=package_source,
                        dependencies=deps,
                    )
                else:
                    pkg = Package(
                        name=package_name,
                        version=version,
                        source=package_source,
                        dependencies=deps,
                    )
            packages_by_name[pkg.full_name] = pkg
            graph.add_node(pkg)  # type: ignore
        for pkg in graph:  # type: ignore
            for dep in pkg.dependencies:
                if dep.package_full_name in packages_by_name:
                    graph.add_edge(pkg, packages_by_name[dep.package_full_name], dependency=dep)  # type: ignore
        graph._collapsed = True
        return graph

    def distance_to(
        self, graph: RootedDiGraph[Package, SourcePackage], normalize: bool = False
    ) -> float:
        if not self._collapsed:
            return self.collapse_versions().distance_to(graph, normalize)
        if not self.source_packages:
            # use our roots instead:
            compare_from: RootedDiGraph[Package, Package] = self.find_roots()
        else:
            compare_from = self  # type: ignore
        if isinstance(graph, DependencyGraph):
            compare_to: RootedDiGraph[Package, Package] = graph.collapse_versions()  # type: ignore
        else:
            compare_to = graph  # type: ignore
        if not compare_to.roots:
            compare_to = compare_to.find_roots()
        if compare_from is self:
            return super().distance_to(compare_to, normalize)
        else:
            return compare_from.distance_to(compare_to, normalize)


class PackageCache(ABC):
    """An abstract base class for a collection of packages"""

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
    def package_full_names(self) -> FrozenSet[str]:
        raise NotImplementedError()

    def latest_match(self, to_match: Union[str, Package, Dependency]) -> Optional[Package]:
        """
        Returns the latest package version that matches the given dependency, or None if no packages match
        """
        latest: Optional[Package] = None
        for p in self.match(to_match):
            if latest is None or p.version >= latest.version:
                latest = p
        return latest

    @abstractmethod
    def match(self, to_match: Union[str, Package, Dependency]) -> Iterator[Package]:
        """
        Yields all packages in this collection of packages that match the Dependency.

        This function does not perform any dependency resolution;
        it only matches against existing packages in this cache.

        """
        raise NotImplementedError()

    def get(
        self,
        source: Union[str, "DependencyResolver"],
        name: str,
        version: Union[str, Version],
    ) -> Optional[Package]:
        pkg = Package(source=source, name=name, version=version)
        it = self.match(pkg.to_dependency())
        try:
            return next(it)
        except StopIteration:
            return None

    def to_graph(self) -> DependencyGraph:
        graph = DependencyGraph()
        for package in self:
            graph.add_node(package)  # type: ignore
            for dep in package.dependencies:
                for p in self.match(dep):
                    assert p in self
                    graph.add_edge(package, p, dependency=dep)  # type: ignore
        return graph

    def to_obj(self):
        def package_to_dict(package: Package):
            ret = {
                "dependencies": {
                    f"{dep.source}:{dep.package}": str(dep.semantic_version)
                    for dep in package.dependencies
                },
                "vulnerabilities": [v.to_compact_str() for v in package.vulnerabilities],
                "source": package.source,
            }
            if isinstance(package, SourcePackage):
                ret["is_source_package"] = True  # type: ignore
            return ret

        return {
            package_full_name: {
                str(package.version): package_to_dict(package)
                for package in self.package_versions(package_full_name)
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
                shape = "triangle" if pkg.vulnerabilities else "rectangle"
                dot.node(pkg_id, label=str(pkg), shape=shape)
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

    def unresolved_dependencies(
        self, packages: Optional[Iterable[Package]] = None
    ) -> Iterable[Dependency]:
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
        self._updated: Dict[Package, Set[str]] = defaultdict(set)  # source:package -> dep

    def __len__(self):
        return sum(sum(map(len, source.values())) for source in self._cache.values())

    def __iter__(self) -> Iterator[Package]:
        return (p for d in self._cache.values() for v in d.values() for p in v.values())

    def updated_by(self, package: Package) -> FrozenSet[str]:
        return frozenset(self._updated[package])

    def was_updated(self, package: Package, resolver: str) -> bool:
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
        package_source, package_name = package_full_name.split(":", 1)
        packages = self._cache[package_source]
        if package_name in packages:
            yield from packages[package_name].values()

    def match(self, to_match: Union[str, Package, Dependency]) -> Iterator[Package]:
        if isinstance(to_match, str):
            to_match = Package.from_string(to_match)
        if isinstance(to_match, Package):
            to_match = to_match.to_dependency()
        assert isinstance(to_match, Dependency)
        source_dict = self._cache.get(to_match.source, {})
        for version, package in source_dict.get(to_match.package, {}).items():
            if to_match.semantic_version is not None and version in to_match.semantic_version:
                yield package

    def add(self, package: Package):
        original_package = (
            self._cache.setdefault(package.source, {})
            .setdefault(package.name, {})
            .get(package.version)
        )
        if original_package is not None:
            package = original_package.update_dependencies(package.dependencies)
        self._cache[package.source][package.name][package.version] = package

    def __str__(self):
        return "[" + ",".join(self.package_full_names()) + "]"


@functools.lru_cache()
def resolvers() -> FrozenSet["DependencyResolver"]:
    """Collection of all the default instances of DependencyResolvers"""
    return frozenset(cls() for cls in DependencyResolver.__subclasses__())  # type: ignore


@functools.lru_cache()
def resolver_by_name(name: str) -> "DependencyResolver":
    """Finds a resolver instance by name. The result is cached."""
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
        logger.info(f"{self} does not implement `resolve()`")
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

    def can_update_dependencies(self, package: Package) -> bool:
        return False

    def update_dependencies(self, package: Package) -> Package:
        return package

    def __hash__(self):
        return hash(self.name)

    def __eq__(self, other):
        return isinstance(other, DependencyResolver) and other.name == self.name


class PackageRepository(InMemoryPackageCache):
    pass


class _DependencyResult:
    def __init__(self, dep: Dependency, packages: List[Package], depth: int):
        self.dep: Dependency = dep
        self.packages: List[Package] = packages
        self.depth: int = depth


def _process_dep(dep: Dependency, depth: int) -> _DependencyResult:
    return _DependencyResult(dep=dep, packages=list(dep.resolver.resolve(dep)), depth=depth)


class _PackageResult:
    def __init__(
        self,
        package: Package,
        was_updated: bool,
        updated_in_resolvers: Iterable[str],
        depth: int,
    ):
        self.package: Package = package
        self.was_updated: bool = was_updated
        self.updated_in_resolvers: Set[str] = set(updated_in_resolvers)
        self.depth: int = depth


def _update_package(package: Package, depth: int) -> _PackageResult:
    old_deps = frozenset(package.dependencies)
    uir: List[str] = []
    for resolver in resolvers():
        if resolver.can_update_dependencies(package):
            package = resolver.update_dependencies(package)
            uir.append(resolver.name)
    return _PackageResult(
        package=package,
        was_updated=package.dependencies != old_deps,
        updated_in_resolvers=uir,
        depth=depth,
    )


def resolve(
    repo_or_spec: Union[Package, Dependency, SourceRepository],
    cache: Optional[PackageCache] = None,
    depth_limit: int = -1,
    repo: Optional[PackageRepository] = None,
    max_workers: Optional[int] = None,
) -> PackageRepository:
    """
    Resolves the dependencies for a package, dependency, or source repository.

    If depth_limit is negative (the default), recursively resolve all dependencies.
    If depth_limit is greater than zero, only recursively resolve dependencies to that depth.
    max_workers controls the number of spawned threads, if None cpu_count is used.
    """
    if depth_limit == 0:
        return PackageRepository()

    if max_workers is None:
        try:
            max_workers = cpu_count()
        except NotImplementedError:
            max_workers = 5

    if repo is None:
        repo = PackageRepository()

    if cache is None:
        cache = InMemoryPackageCache()  # Some resolvers may use it to save temporary results

    try:
        with cache, tqdm(
            desc=f"resolving {repo_or_spec!s}", leave=False, unit=" dependencies"
        ) as t:
            if isinstance(repo_or_spec, Dependency):
                unresolved_dependencies: List[Tuple[Dependency, int]] = [(repo_or_spec, 0)]
                unupdated_packages: List[Tuple[Package, int]] = []
            elif isinstance(repo_or_spec, Package):
                unresolved_dependencies = []
                unupdated_packages = [(repo_or_spec, 0)]
            elif isinstance(repo_or_spec, SourceRepository):
                # repo_or_spec is a SourceRepository
                unresolved_dependencies = []
                unupdated_packages = []
                found_source_package = False
                for resolver in resolvers():
                    if resolver.can_resolve_from_source(repo_or_spec):
                        source_package = resolver.resolve_from_source(repo_or_spec, cache=cache)
                        if source_package is None:
                            continue
                        found_source_package = True
                        unupdated_packages.append((source_package, 0))
                if not found_source_package:
                    raise ValueError(f"Can not resolve {repo_or_spec}")
            else:
                raise ValueError(
                    f"repo_or_spec must be either a Package, Dependency, or SourceRepository"
                )

            t.total = len(unupdated_packages) + len(unresolved_dependencies)

            futures: Set[Future[Union[_DependencyResult, _PackageResult]]] = set()
            queued: Set[Dependency] = {d for d, _ in unresolved_dependencies}
            if max_workers > 1:
                pool = ThreadPoolExecutor(
                    max_workers=max_workers, thread_name_prefix="it-depends-resolver"
                )

            def process_updated_package(
                updated_package: Package,
                at_depth: int,
                updated_in_resolvers: Set[str],
                was_updated: bool = True,
            ):
                repo.add(updated_package)  # type: ignore
                if (
                    not isinstance(updated_package, SourcePackage)
                    and updated_package is not repo_or_spec
                ):
                    if was_updated:
                        cache.add(updated_package)  # type: ignore
                    for r in updated_in_resolvers:
                        repo.set_updated(updated_package, r)  # type: ignore
                        cache.set_updated(updated_package, r)  # type: ignore
                if depth_limit < 0 or at_depth < depth_limit:
                    new_deps = {d for d in updated_package.dependencies if d not in queued}
                    unresolved_dependencies.extend((d, at_depth + 1) for d in sorted(new_deps))
                    t.total += len(new_deps)
                    queued.update(new_deps)

            def process_resolution(
                dep: Dependency,
                packages: Iterable[Package],
                at_depth: int,
                already_cached: bool = False,
            ):
                """This gets called whenever we resolve a new package"""
                repo.set_resolved(dep)  # type: ignore
                packages = list(packages)
                if not already_cached and cache is not None and dep is not repo_or_spec:
                    cache.set_resolved(dep)
                    cache.extend(packages)
                unupdated_packages.extend((p, at_depth) for p in packages)
                t.total += len(packages)

            while unresolved_dependencies or unupdated_packages or futures:
                # while there are more unresolved dependencies, unupdated packages,
                # or concurrent jobs that are still running:

                reached_fixed_point = cache is None
                while not reached_fixed_point:
                    reached_fixed_point = True

                    # loop through the unupdated packages and see if any are cached:
                    not_updated: List[Tuple[Package, int]] = []
                    was_updatable = False
                    for package, depth in unupdated_packages:
                        for resolver in resolvers():
                            if resolver.can_update_dependencies(package):
                                was_updatable = True
                                if not cache.was_updated(package, resolver.name):
                                    not_updated.append((package, depth))
                                    break
                        else:
                            if was_updatable:
                                # every resolver that could have updated this package did update it in the cache
                                try:
                                    # retrieve the package from the cache
                                    package = next(iter(cache.match(package)))
                                except StopIteration:
                                    pass
                            process_updated_package(package, depth, updated_in_resolvers=set())
                            t.update(1)

                    if unupdated_packages != not_updated:
                        reached_fixed_point = False
                        unupdated_packages = not_updated

                    # loop through the unresolved deps and see if any are cached:
                    not_cached: List[Tuple[Dependency, int]] = []
                    for dep, depth in unresolved_dependencies:
                        if dep is not repo_or_spec and cache.was_resolved(dep):
                            matches = cache.match(dep)
                            process_resolution(dep, matches, depth, already_cached=True)
                            t.update(1)
                        else:
                            not_cached.append((dep, depth))
                    if unresolved_dependencies != not_cached:
                        reached_fixed_point = False
                        unresolved_dependencies = not_cached

                if max_workers <= 1:
                    # don't use concurrency
                    if unupdated_packages:
                        t.update(1)
                        pkg_result = _update_package(*unupdated_packages[0])
                        unupdated_packages = unupdated_packages[1:]
                        process_updated_package(
                            pkg_result.package,
                            pkg_result.depth,
                            pkg_result.updated_in_resolvers,
                            pkg_result.was_updated,
                        )
                    if unresolved_dependencies:
                        t.update(1)
                        dep_result = _process_dep(*unresolved_dependencies[0])
                        unresolved_dependencies = unresolved_dependencies[1:]
                        process_resolution(dep_result.dep, dep_result.packages, dep_result.depth)
                else:
                    # new_jobs is the number of new concurrent resolutions we can start without exceeding max_workers
                    new_jobs = max_workers - len(futures)
                    # create `new_jobs` package update jobs:
                    futures |= {  # type: ignore
                        pool.submit(_update_package, package, depth)
                        for package, depth in unupdated_packages[:new_jobs]
                    }
                    unupdated_packages = unupdated_packages[new_jobs:]
                    new_jobs = max_workers - len(futures)
                    # create `new_jobs` new resolution jobs:
                    futures |= {  # type: ignore
                        pool.submit(_process_dep, dep, depth)
                        for dep, depth in unresolved_dependencies[:new_jobs]
                    }
                    unresolved_dependencies = unresolved_dependencies[new_jobs:]
                    if futures:
                        done, futures = wait(futures, return_when=FIRST_COMPLETED)
                        for finished in done:
                            t.update(1)
                            result = finished.result()
                            if isinstance(result, _PackageResult):
                                process_updated_package(
                                    result.package,
                                    result.depth,
                                    result.updated_in_resolvers,
                                    result.was_updated,
                                )
                            elif isinstance(result, _DependencyResult):
                                process_resolution(result.dep, result.packages, result.depth)
                            else:
                                raise NotImplementedError(f"Unexpected future result: {result!r}")

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
