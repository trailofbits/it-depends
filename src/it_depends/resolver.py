"""Dependency resolver module for managing package dependencies."""

from __future__ import annotations

import functools
import logging
from abc import ABC, abstractmethod
from collections import defaultdict
from dataclasses import dataclass
from typing import TYPE_CHECKING

from semantic_version import SimpleSpec, Version
from semantic_version.base import AllOf, BaseSpec

from .models import Dependency

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator

    from .models import Package, SourcePackage
    from .repository import SourceRepository

logger = logging.getLogger(__name__)


@dataclass
class DockerSetup:
    """Docker setup configuration for resolvers."""

    apt_get_packages: list[str]
    install_package_script: str
    load_package_script: str = ""
    baseline_script: str = ""
    post_install: str = ""


class ResolverAvailability:
    """Represents the availability status of a resolver."""

    def __init__(self, *, is_available: bool, reason: str = "") -> None:
        """Initialize resolver availability.

        Args:
            is_available: Whether the resolver is available
            reason: Reason for unavailability if not available

        """
        if not is_available and not reason:
            error_msg = "You must provide a reason if `not is_available`"
            raise ValueError(error_msg)
        self.is_available: bool = is_available
        self.reason: str = reason

    def __bool__(self) -> bool:
        """Return availability status."""
        return self.is_available


class DependencyResolver(ABC):
    """Find a set of Packages that agrees with a Dependency specification."""

    name: str
    description: str
    _instance: DependencyResolver | None = None

    def __new__(cls, *args: object, **kwargs: object) -> DependencyResolver:  # noqa: PYI034
        """Create a singleton instance."""
        if not isinstance(cls._instance, cls):
            cls._instance = super().__new__(cls, *args, **kwargs)
        return cls._instance

    def __init_subclass__(cls, **kwargs: object) -> None:
        """Validate subclass configuration."""
        if not hasattr(cls, "name") or cls.name is None:
            error_msg = f"{cls.__name__} must define a `name` class member"
            raise TypeError(error_msg)
        if not hasattr(cls, "description") or cls.description is None:
            error_msg = f"{cls.__name__} must define a `description` class member"
            raise TypeError(error_msg)
        resolvers.cache_clear()

    @abstractmethod
    def resolve(self, dependency: Dependency) -> Iterator[Package]:
        """Yield all packages that satisfy the given dependency."""
        error_msg = f"{self} does not implement `resolve()`"
        logger.info(error_msg)
        raise NotImplementedError

    @classmethod
    def parse_spec(cls, spec: str) -> SimpleSpec:
        """Parse a semantic version string into a semantic version object for this specific resolver."""
        return SimpleSpec.parse(spec)

    @classmethod
    def parse_version(cls, version_string: str) -> Version:
        """Parse a version string into a version object for this specific resolver."""
        return Version.coerce(version_string)

    def docker_setup(self) -> DockerSetup | None:
        """Return an optional docker setup for running this resolver."""
        return None

    def is_available(self) -> ResolverAvailability:
        """Check if this resolver is available."""
        return ResolverAvailability(is_available=True)

    @abstractmethod
    def can_resolve_from_source(self, repo: SourceRepository) -> bool:
        """Check if this resolver can resolve from source."""
        raise NotImplementedError

    @abstractmethod
    def resolve_from_source(self, repo: SourceRepository, cache: object | None = None) -> SourcePackage | None:
        """Resolve any new `SourcePackage`s in this repo."""
        raise NotImplementedError

    def can_update_dependencies(self, _package: Package) -> bool:
        """Check if this resolver can update dependencies."""
        return False

    def update_dependencies(self, package: Package) -> Package:
        """Update dependencies for a package."""
        return package

    def __hash__(self) -> int:
        """Return hash of the resolver."""
        return hash(self.name)

    def __eq__(self, other: object) -> bool:
        """Check if two resolvers are equal."""
        return isinstance(other, DependencyResolver) and other.name == self.name


class CompoundSpec(BaseSpec):
    """Represents a compound spec."""

    def __init__(self, *to_combine: BaseSpec) -> None:
        """Initialize a compound spec."""
        super(CompoundSpec, self).__init__(",".join(s.expression for s in to_combine))  # noqa: UP008
        self.clause = AllOf(*(s.clause for s in to_combine))

    @classmethod
    def _parse_to_clause(cls, expression: str) -> BaseSpec:  # noqa: ARG003
        """Convert an expression to a clause."""
        # Placeholder, we actually set self.clause in self.__init__
        return


class PackageSet:
    """Represents a set of packages and their dependencies."""

    def __init__(self) -> None:
        """Initialize the package set."""
        self._packages: dict[tuple[str, str], Package] = {}
        self._unsatisfied: dict[tuple[str, str], dict[Dependency, set[Package]]] = defaultdict(lambda: defaultdict(set))
        self.is_valid: bool = True
        self.is_complete: bool = True

    def __eq__(self, other: object) -> bool:
        """Check if two package sets are equal."""
        return isinstance(other, PackageSet) and self._packages.values() == other._packages.values()

    def __hash__(self) -> int:
        """Return hash of the package set."""
        return hash(frozenset(self._packages.values()))

    def __len__(self) -> int:
        """Return number of packages in the package set."""
        return len(self._packages)

    def __iter__(self) -> Iterator[Package]:
        """Iterate over packages in the package set."""
        yield from self._packages.values()

    def __contains__(self, package: Package) -> bool:
        """Check if a package is in the package set."""
        pkg_spec = (package.name, package.source)
        return pkg_spec in self._packages and self._packages[pkg_spec] == package

    def unsatisfied_dependencies(self) -> Iterator[tuple[Dependency, frozenset[Package]]]:
        """Iterate over unsatisfied dependencies in the package set."""
        for (pkg_name, pkg_source), deps in sorted(
            # try the dependencies with the most options first
            self._unsatisfied.items(),
            key=lambda x: (len(x[1]), x[0]),
        ):
            if len(deps) == 0:
                continue
            elif len(deps) == 1:
                dep, packages = next(iter(deps.items()))
            else:
                # there are multiple requirements for the same dependency
                spec = CompoundSpec(*(d.semantic_version for d in deps.keys()))  # noqa: SIM118
                dep = Dependency(pkg_name, pkg_source, spec)
                packages = {p for packages in deps.values() for p in packages}

            yield dep, frozenset(packages)

    def copy(self) -> PackageSet:
        """Copy the package set."""
        ret = PackageSet()
        ret._packages = self._packages.copy()
        ret._unsatisfied = defaultdict(lambda: defaultdict(set))
        for dep_spec, deps in self._unsatisfied.items():
            ret._unsatisfied[dep_spec] = defaultdict(set)
            for dep, packages in deps.items():
                ret._unsatisfied[dep_spec][dep] = set(packages)
                assert all(p in ret for p in packages)  # noqa: S101
        ret.is_valid = self.is_valid
        ret.is_complete = self.is_complete
        return ret

    def add(self, package: Package) -> None:
        """Add a package to the package set."""
        pkg_spec = (package.name, package.source)
        if pkg_spec in self._packages and self._packages[pkg_spec].version != package.version:
            self.is_valid = False
        if not self.is_valid:
            return
        self._packages[pkg_spec] = package
        if pkg_spec in self._unsatisfied:
            # there are some existing packages that have unsatisfied dependencies that could be
            # satisfied by this new package
            for dep in list(self._unsatisfied[pkg_spec].keys()):
                if dep.match(package):
                    del self._unsatisfied[pkg_spec][dep]
                    if len(self._unsatisfied[pkg_spec]) == 0:
                        del self._unsatisfied[pkg_spec]
        # add any new unsatisfied dependencies for this package
        for dep in package.dependencies:
            dep_spec = (dep.package, dep.source)
            if dep_spec not in self._packages:
                self._unsatisfied[dep_spec][dep].add(package)
            elif not dep.match(self._packages[dep_spec]):
                self.is_valid = False
                break

        self.is_complete = self.is_valid and len(self._unsatisfied) == 0


class PartialResolution:
    """Represents a partial resolution of dependencies."""

    def __init__(
        self,
        packages: Iterable[Package] = (),
        dependencies: Iterable[Package] = (),
        parent: PartialResolution | None = None,
    ) -> None:
        """Initialize partial resolution."""
        self._packages: frozenset[Package] = frozenset(packages)
        self._dependencies: frozenset[Package] = frozenset(dependencies)
        self.parent: PartialResolution | None = parent
        if self.parent is not None:
            self.packages: PackageSet = self.parent.packages.copy()  # type: ignore[method-assign,attr-defined]
        else:
            self.packages = PackageSet()  # type: ignore[method-assign,assignment]
        for package in self._packages:
            self.packages.add(package)  # type: ignore[attr-defined]
            if not self.is_valid:
                break
        if self.is_valid:
            for dep in self._dependencies:
                self.packages.add(dep)  # type: ignore[attr-defined]
                if not self.is_valid:
                    break

    @property
    def is_valid(self) -> bool:
        """Check if the resolution is valid."""
        return self.packages.is_valid  # type: ignore[attr-defined,no-any-return]

    @property
    def is_complete(self) -> bool:
        """Check if the resolution is complete."""
        return self.packages.is_complete  # type: ignore[attr-defined,no-any-return]

    def __contains__(self, package: Package) -> bool:
        """Check if package is in the resolution."""
        return package in self.packages  # type: ignore[operator]

    def add(self, packages: Iterable[Package], depends_on: Package) -> PartialResolution:
        """Add packages and dependencies to the resolution."""
        return PartialResolution(packages, (depends_on,), parent=self)

    def packages(self) -> Iterator[Package]:
        """Iterate over packages in the resolution."""
        yield from self.packages  # type: ignore[misc]

    __iter__ = packages

    def dependencies(self) -> Iterator[tuple[Package, Package]]:
        """Iterate over dependencies in the resolution."""
        pr: PartialResolution | None = self
        while pr is not None:
            for depends_on in sorted(pr._dependencies):  # noqa: SLF001
                for package in pr._packages:  # noqa: SLF001
                    yield package, depends_on
            pr = pr.parent

    def __len__(self) -> int:
        """Return number of packages in the resolution."""
        return len(self.packages)  # type: ignore[arg-type]

    def __eq__(self, other: object) -> bool:
        """Check if two resolutions are equal."""
        return isinstance(other, PartialResolution) and self.packages == other.packages

    def __hash__(self) -> int:
        """Return hash of the resolution."""
        return hash(self.packages)


@functools.lru_cache
def resolvers() -> frozenset[DependencyResolver]:
    """Get collection of all the default instances of DependencyResolvers."""
    return frozenset(
        cls()  # type: ignore[abstract]
        for cls in DependencyResolver.__subclasses__()
    )


@functools.lru_cache
def resolver_by_name(name: str) -> DependencyResolver:
    """Find a resolver instance by name. The result is cached."""
    for instance in resolvers():
        if instance.name == name:
            return instance
    raise KeyError(name)


def is_known_resolver(name: str) -> bool:
    """Check if name is a valid/known resolver name."""
    try:
        resolver_by_name(name)
    except KeyError:
        return False
    else:
        return True
