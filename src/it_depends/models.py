import json
from collections.abc import Iterable
from typing import (
    Dict,
    FrozenSet,
    List,
    Union,
)

from semantic_version import SimpleSpec, Version
from semantic_version.base import BaseSpec as SemanticVersion

from .repository import SourceRepository
from .resolver import DependencyResolver, is_known_resolver, resolver_by_name


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
        if not isinstance(other, Dependency) or (self.package != other.package and self.source != other.source):
            return False
        return self.semantic_version.clause.includes(other.semantic_version.clause)

    def __hash__(self):
        return hash((self.source, self.package, self.semantic_version))

    def match(self, package: "Package") -> bool:
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

    def __init__(
        self,
        package: str,
        alias_name: str,
        source: Union[str, "DependencyResolver"],
        semantic_version: SemanticVersion = SimpleSpec("*"),
    ):
        self.alias_name = alias_name
        super().__init__(package, source, semantic_version)

    def __eq__(self, other: object) -> bool:
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
        """The initial main resolver for this package.
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
            "dependencies": {f"{dep.source}:{dep.package}": str(dep.semantic_version) for dep in self.dependencies},
            "vulnerabilities": [vuln.to_obj() for vuln in self.vulnerabilities],
        }
        return ret  # type: ignore

    def dumps(self) -> str:
        return json.dumps(self.to_obj())

    def same_package(self, other: "Package") -> bool:
        """Checks if two packages are the same, but potentially different versions"""
        return self.name == other.name and self.source == other.source

    def __eq__(self, other):
        if isinstance(other, Package):
            return other.name == self.name and other.source == self.source and other.version == self.version
        return False

    def __lt__(self, other):
        return isinstance(other, Package) and (self.name, self.source, self.version) < (
            other.name,
            other.source,
            other.version,
        )

    def __hash__(self):
        return hash((self.version, self.name, self.version))


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
