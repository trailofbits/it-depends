"""Dependency resolver module for managing package dependencies."""

from __future__ import annotations

import functools
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING

from semantic_version import SimpleSpec, Version

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator

    from .models import Dependency, Package, SourcePackage
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
            self.packages: set[Package] = self.parent.packages.copy()
        else:
            self.packages = set()
        for package in self._packages:
            self.packages.add(package)
            if not self.is_valid:
                break
        if self.is_valid:
            for dep in self._dependencies:
                self.packages.add(dep)
                if not self.is_valid:
                    break

    @property
    def is_valid(self) -> bool:
        """Check if the resolution is valid."""
        # Check if there are any conflicting packages
        package_names = {pkg.name for pkg in self.packages}
        return len(package_names) == len(self.packages)

    @property
    def is_complete(self) -> bool:
        """Check if the resolution is complete."""
        # For now, assume all resolutions are complete
        return True

    def __contains__(self, package: Package) -> bool:
        """Check if package is in the resolution."""
        return package in self.packages

    def add(self, packages: Iterable[Package], depends_on: Package) -> PartialResolution:
        """Add packages and dependencies to the resolution."""
        return PartialResolution(packages, (depends_on,), parent=self)

    def get_packages(self) -> Iterator[Package]:
        """Iterate over packages in the resolution."""
        yield from self.packages

    __iter__ = get_packages

    @property
    def dependencies_set(self) -> frozenset[Package]:
        """Get the set of dependencies."""
        return self._dependencies

    @property
    def packages_set(self) -> frozenset[Package]:
        """Get the set of packages."""
        return self._packages

    def dependencies(self) -> Iterator[tuple[Package, Package]]:
        """Iterate over dependencies in the resolution."""
        pr: PartialResolution | None = self
        while pr is not None:
            # Access the private members through the class since they're needed for iteration
            for depends_on in sorted(pr.dependencies_set):
                for package in pr.packages_set:
                    yield package, depends_on
            pr = pr.parent

    def __len__(self) -> int:
        """Return number of packages in the resolution."""
        return len(self.packages)

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
        if not getattr(cls, "__abstractmethods__", None) and not cls.__abstractmethods__
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
