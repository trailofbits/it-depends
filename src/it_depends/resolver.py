import functools
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import FrozenSet, Iterator, Optional, TYPE_CHECKING

from semantic_version import SimpleSpec, Version
from semantic_version.base import BaseSpec as SemanticVersion

from .repository import SourceRepository

if TYPE_CHECKING:
    from .models import Package, Dependency


@dataclass
class DockerSetup:
    apt_get_packages: list[str]
    install_package_script: str
    load_package_script: str
    baseline_script: str
    post_install: str = ""


class ResolverAvailability:
    def __init__(self, is_available: bool, reason: str = ""):
        if not is_available and not reason:
            raise ValueError("You must provide a reason if `not is_available`")
        self.is_available: bool = is_available
        self.reason: str = reason

    def __bool__(self):
        return self.is_available


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
    def resolve(self, dependency: "Dependency") -> Iterator["Package"]:
        """Yields all packages that satisfy the given dependency"""
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
        self, repo: SourceRepository, cache=None
    ) -> Optional["SourcePackage"]:
        """Resolves any new `SourcePackage`s in this repo"""
        raise NotImplementedError()

    def can_update_dependencies(self, package: "Package") -> bool:
        return False

    def update_dependencies(self, package: "Package") -> "Package":
        return package

    def __hash__(self):
        return hash(self.name)

    def __eq__(self, other):
        return isinstance(other, DependencyResolver) and other.name == self.name


@functools.lru_cache()
def resolvers() -> FrozenSet[DependencyResolver]:
    """Collection of all the default instances of DependencyResolvers"""
    return frozenset(cls() for cls in DependencyResolver.__subclasses__())


@functools.lru_cache()
def resolver_by_name(name: str) -> DependencyResolver:
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
