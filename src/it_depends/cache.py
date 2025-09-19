"""Package cache implementations for dependency resolution."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections import defaultdict
from typing import TYPE_CHECKING, Self

from graphviz import Digraph

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator

    from .graph import DependencyGraph
    from .models import Dependency, Package, SourcePackage, Version
    from .resolver import DependencyResolver
else:
    from .graph import DependencyGraph
    from .models import Package


class PackageCache(ABC):
    """An abstract base class for a collection of packages."""

    def __init__(self) -> None:
        """Initialize package cache."""
        self._entries: int = 0

    @abstractmethod
    def open(self) -> None:
        """Open the cache."""

    @abstractmethod
    def close(self) -> None:
        """Close the cache."""

    def __enter__(self) -> Self:
        """Enter context manager."""
        if self._entries == 0:
            self.open()
        self._entries += 1
        return self

    def __exit__(self, exc_type: type[BaseException] | None, exc_val: BaseException | None, exc_tb: object) -> None:
        """Exit context manager."""
        self._entries -= 1
        if self._entries == 0:
            self.close()

    @abstractmethod
    def __len__(self) -> int:
        """Return the number of packages in this cache."""
        raise NotImplementedError

    @abstractmethod
    def __iter__(self) -> Iterator[Package]:
        """Iterate over the packages in this cache."""
        raise NotImplementedError

    def __contains__(self, pkg: Package) -> bool:
        """Check if package exists in this collection of packages."""
        return any(pkg_i == pkg for pkg_i in self)

    @abstractmethod
    def was_resolved(self, dependency: Dependency) -> bool:
        """Check if this particular dependency was resolved."""
        raise NotImplementedError

    @abstractmethod
    def set_resolved(self, dependency: Dependency) -> None:
        """Mark this particular dependency as resolved."""
        raise NotImplementedError

    @abstractmethod
    def set_updated(self, package: Package, resolver: str) -> None:
        """Mark package as updated by resolver."""
        raise NotImplementedError

    @abstractmethod
    def was_updated(self, package: Package, resolver: str) -> bool:
        """Check if package was updated by resolver."""
        raise NotImplementedError

    @abstractmethod
    def updated_by(self, package: Package) -> frozenset[str]:
        """Get the set of resolver names that updated package."""
        raise NotImplementedError

    @abstractmethod
    def package_versions(self, package_full_name: str) -> Iterator[Package]:
        """Get all versions of a package by full name."""
        raise NotImplementedError

    @abstractmethod
    def package_full_names(self) -> frozenset[str]:
        """Get all package full names in the cache."""
        raise NotImplementedError

    def latest_match(self, to_match: str | Package | Dependency) -> Package | None:
        """Return the latest package version that matches the given dependency, or None if no packages match."""
        latest: Package | None = None
        for p in self.match(to_match):
            if latest is None or p.version >= latest.version:
                latest = p
        return latest

    @abstractmethod
    def match(self, to_match: str | Package | Dependency) -> Iterator[Package]:
        """Yield all packages in this collection of packages that match the Dependency.

        This function does not perform any dependency resolution;
        it only matches against existing packages in this cache.

        """
        raise NotImplementedError

    def get(
        self,
        source: str | DependencyResolver,
        name: str,
        version: str | Version,
    ) -> Package | None:
        """Get a package from the cache."""
        pkg = Package(source=source, name=name, version=version)
        it = self.match(pkg.to_dependency())
        try:
            return next(it)
        except StopIteration:
            return None

    def to_graph(self) -> DependencyGraph:
        """Convert cache to dependency graph."""
        graph = DependencyGraph()
        for package in self:
            graph.add_node(package)
            for dep in package.dependencies:
                for p in self.match(dep):
                    if p not in self:
                        msg = "Package not in cache"
                        raise AssertionError(msg)
                    graph.add_edge(package, p, dependency=dep)
        return graph

    def to_obj(self) -> dict[str, dict[str, dict[str, str | bool]]]:
        """Convert cache to object representation."""

        def package_to_dict(package: Package) -> dict[str, str | bool]:
            ret = {
                "dependencies": {
                    f"{dep.source}:{dep.package}": str(dep.semantic_version) for dep in package.dependencies
                },
                "vulnerabilities": [v.to_compact_str() for v in package.vulnerabilities],
                "source": package.source,
            }
            if hasattr(package, "source_repo"):  # SourcePackage
                ret["is_source_package"] = True
            return ret

        return {
            package_full_name: {
                str(package.version): package_to_dict(package) for package in self.package_versions(package_full_name)
            }
            for package_full_name in self.package_full_names()
        }

    @property
    def source_packages(self) -> set[SourcePackage]:
        """Get all source packages in the cache."""
        return {package for package in self if hasattr(package, "source_repo")}

    def to_dot(self, sources: Iterable[Package] | None = None) -> Digraph:  # noqa: C901
        """Render a Graphviz Dot graph of the dependency hierarchy.

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
        package_ids: dict[Package, str] = {}
        dependency_ids: dict[Dependency, str] = {}

        def add_package(pkg: Package) -> str:
            if pkg not in package_ids:
                pkg_id = f"package{len(package_ids)}"
                package_ids[pkg] = pkg_id
                shape = "triangle" if pkg.vulnerabilities else "rectangle"
                dot.node(pkg_id, label=str(pkg), shape=shape)
                return pkg_id
            return package_ids[pkg]

        def add_dependency(dep: Dependency) -> str:
            if dep not in dependency_ids:
                dep_id = f"dep{len(dependency_ids)}"
                dependency_ids[dep] = dep_id
                dot.node(dep_id, label=str(dep), shape="oval")
                return dep_id
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
    def add(self, package: Package) -> None:
        """Add a package to the cache."""
        raise NotImplementedError

    def extend(self, packages: Iterable[Package]) -> None:
        """Add multiple packages to the cache."""
        for package in packages:
            self.add(package)

    def unresolved_dependencies(self, packages: Iterable[Package] | None = None) -> Iterable[Dependency]:
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
    """In-memory implementation of package cache."""

    def __init__(self, _cache: dict[str, dict[str, dict[Version, Package]]] | None = None) -> None:
        """Initialize in-memory package cache."""
        super().__init__()
        if _cache is None:
            self._cache: dict[str, dict[str, dict[Version, Package]]] = {}
        else:
            self._cache = _cache
        self._resolved: dict[str, set[Dependency]] = defaultdict(set)  # source:package -> dep
        self._updated: dict[Package, set[str]] = defaultdict(set)  # source:package -> dep

    def __len__(self) -> int:
        """Return the number of packages in the cache."""
        return sum(sum(map(len, source.values())) for source in self._cache.values())

    def __iter__(self) -> Iterator[Package]:
        """Iterate over all packages in the cache."""
        return (p for d in self._cache.values() for v in d.values() for p in v.values())

    def updated_by(self, package: Package) -> frozenset[str]:
        """Get the set of resolvers that updated this package."""
        return frozenset(self._updated[package])

    def was_updated(self, package: Package, resolver: str) -> bool:
        """Check if package was updated by resolver."""
        return resolver in self._updated[package]

    def set_updated(self, package: Package, resolver: str) -> None:
        """Mark package as updated by resolver."""
        self._updated[package].add(resolver)

    def was_resolved(self, dependency: Dependency) -> bool:
        """Check if dependency was resolved."""
        return dependency in self._resolved[f"{dependency.source}:{dependency.package}"]

    def set_resolved(self, dependency: Dependency) -> None:
        """Mark dependency as resolved."""
        self._resolved[f"{dependency.source}:{dependency.package}"].add(dependency)

    def from_source(self, source: str | DependencyResolver) -> PackageCache:
        """Get a cache filtered by source."""
        if hasattr(source, "name"):
            source = source.name
        return InMemoryPackageCache({source: self._cache.setdefault(source, {})})

    def package_full_names(self) -> frozenset[str]:
        """Get all package full names in the cache."""
        ret: set[str] = set()
        for source, versions in self._cache.items():
            for name in versions:
                ret.add(f"{source}:{name}")
        return frozenset(ret)

    def package_versions(self, package_full_name: str) -> Iterator[Package]:
        """Get all versions of a package by full name."""
        package_source, package_name = package_full_name.split(":", 1)
        packages = self._cache[package_source]
        if package_name in packages:
            yield from packages[package_name].values()

    def match(self, to_match: str | Package | Dependency) -> Iterator[Package]:
        """Match packages against a pattern."""
        if isinstance(to_match, str):
            to_match = Package.from_string(to_match)
        if hasattr(to_match, "to_dependency"):  # Package
            to_match = to_match.to_dependency()
        if not hasattr(to_match, "source"):  # Dependency
            msg = "Expected Dependency object"
            raise AssertionError(msg)
        source_dict = self._cache.get(to_match.source, {})
        for version, package in source_dict.get(to_match.package, {}).items():
            if to_match.semantic_version is not None and version in to_match.semantic_version:
                yield package

    def add(self, package: Package) -> None:
        """Add a package to the cache."""
        original_package = self._cache.setdefault(package.source, {}).setdefault(package.name, {}).get(package.version)
        if original_package is not None:
            package = original_package.update_dependencies(package.dependencies)
        self._cache[package.source][package.name][package.version] = package

    def __str__(self) -> str:
        """Return string representation of the cache."""
        return "[" + ",".join(self.package_full_names()) + "]"


class PackageRepository(InMemoryPackageCache):
    """Package repository implementation."""
