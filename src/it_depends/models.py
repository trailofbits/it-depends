"""Core data models for dependency resolution."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterable

    from .resolver import DependencyResolver

from semantic_version import SimpleSpec, Version
from semantic_version.base import BaseSpec as SemanticVersion

# Module-level constants to avoid function calls in defaults
_WILDCARD_SPEC = SimpleSpec("*")

if TYPE_CHECKING:
    from .repository import SourceRepository


class Vulnerability:
    """Represents a specific vulnerability."""

    def __init__(self, vuln_id: str, aliases: Iterable[str], summary: str) -> None:
        """Initialize a vulnerability."""
        self.id = vuln_id
        self.aliases = list(aliases)
        self.summary = summary

    def to_compact_str(self) -> str:
        """Return a compact string representation of the vulnerability."""
        return f"{self.id} ({', '.join(self.aliases)})"

    def to_obj(self) -> dict[str, str | list[str]]:
        """Convert vulnerability to dictionary representation."""
        return {"id": self.id, "aliases": self.aliases, "summary": self.summary}

    def __eq__(self, other: object) -> bool:
        """Check equality with another vulnerability."""
        if isinstance(other, Vulnerability):
            return self.id == other.id
        return False

    def __hash__(self) -> int:
        """Compute hash for vulnerability."""
        return hash((self.id, "".join(self.aliases), self.summary))

    def __lt__(self, other: object) -> bool:
        """Compare vulnerabilities for sorting."""
        if not isinstance(other, Vulnerability):
            msg = "Need a Vulnerability"
            raise TypeError(msg)
        return self.id < other.id


class Dependency:
    """Represents a dependency with package name, source, and version constraints."""

    def __init__(
        self,
        package: str,
        source: str | DependencyResolver,
        semantic_version: SemanticVersion = _WILDCARD_SPEC,
    ) -> None:
        """Initialize a dependency.

        Args:
            package: Name of the package
            source: Source resolver name or resolver instance
            semantic_version: Version constraint specification

        """
        if not isinstance(semantic_version, SemanticVersion):
            msg = "semantic_version must be a SemanticVersion instance"
            raise TypeError(msg)
        from .resolver import DependencyResolver, is_known_resolver  # noqa: PLC0415

        if isinstance(source, DependencyResolver):
            source = source.name
        if not is_known_resolver(source):
            msg = f"{source} is not a known resolver"
            raise ValueError(msg)
        self.source: str = source
        self.package: str = package
        self.semantic_version: SemanticVersion = semantic_version

    @property
    def package_full_name(self) -> str:
        """Get the full package name including source."""
        return f"{self.source}:{self.package}"

    @property
    def resolver(self) -> DependencyResolver:
        """Get the resolver for this dependency's source."""
        from .resolver import resolver_by_name  # noqa: PLC0415

        return resolver_by_name(self.source)

    @classmethod
    def from_string(cls, description: str) -> Dependency:
        """Create a dependency from a string description.

        Args:
            description: String in format "source:package@version"

        Returns:
            New Dependency instance

        """
        try:
            source, tail = description.split(":", 1)
            package, *remainder = tail.split("@", 1)
            version_string = "@".join(remainder)
            if version_string:
                from .resolver import resolver_by_name  # noqa: PLC0415

                resolver = resolver_by_name(source)
                version = resolver.parse_spec(version_string)
            else:
                version = SimpleSpec("*")
        except Exception as e:
            msg = f"Can not parse dependency description <{description}>"
            raise ValueError(msg) from e
        return cls(source=source, package=package, semantic_version=version)

    def __str__(self) -> str:
        """Return string representation of the dependency."""
        return f"{self.source}:{self.package}@{self.semantic_version!s}"

    def __eq__(self, other: object) -> bool:
        """Check equality with another dependency."""
        return (
            isinstance(other, Dependency)
            and self.package == other.package
            and self.source == other.source
            and self.semantic_version == other.semantic_version
        )

    def __lt__(self, other: object) -> bool:
        """Compare dependencies for sorting."""
        if not isinstance(other, Dependency):
            msg = "Need a Dependency"
            raise TypeError(msg)
        return str(self) < str(other)

    def includes(self, other: Dependency) -> bool:
        """Check if this dependency includes another dependency."""
        if not isinstance(other, Dependency) or (self.package != other.package and self.source != other.source):
            return False
        return bool(self.semantic_version.clause.includes(other.semantic_version.clause))

    def __hash__(self) -> int:
        """Compute hash for dependency."""
        return hash((self.source, self.package, self.semantic_version))

    def match(self, package: Package) -> bool:
        """Check if package is a solution for this dependency.

        Args:
            package: Package to check against this dependency

        Returns:
            True if package satisfies this dependency

        """
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
        source: str | DependencyResolver,
        semantic_version: SemanticVersion = _WILDCARD_SPEC,
    ) -> None:
        """Initialize an aliased dependency.

        Args:
            package: Name of the package
            alias_name: Alias name for the package
            source: Source resolver name or resolver instance
            semantic_version: Version constraint specification

        """
        self.alias_name = alias_name
        super().__init__(package, source, semantic_version)

    def __eq__(self, other: object) -> bool:
        """Check equality with another aliased dependency."""
        return isinstance(other, AliasedDependency) and self.alias_name == other.alias_name and super().__eq__(other)

    def __hash__(self) -> int:
        """Compute hash for aliased dependency."""
        return hash((self.alias_name, super().__hash__()))

    def __str__(self) -> str:
        """Get string representation of aliased dependency."""
        return f"{self.source}:{self.alias_name}@{self.package}@{self.semantic_version!s}"


class Package:
    """Represents a package with its dependencies and vulnerabilities."""

    def __init__(
        self,
        name: str,
        version: str | Version,
        source: str | DependencyResolver,
        dependencies: Iterable[Dependency] = (),
        vulnerabilities: Iterable[Vulnerability] = (),
    ) -> None:
        """Initialize a package.

        Args:
            name: Package name
            version: Package version
            source: Source resolver name or resolver instance
            dependencies: Package dependencies
            vulnerabilities: Known vulnerabilities

        """
        if isinstance(version, str):
            version = Version(version)
        self.name: str = name
        self.version: Version = version
        self.dependencies: frozenset[Dependency] = frozenset(dependencies)
        from .resolver import DependencyResolver  # noqa: PLC0415

        if isinstance(source, DependencyResolver):
            self.source: str = source.name
        else:
            self.source = source
        self.vulnerabilities: frozenset[Vulnerability] = frozenset(vulnerabilities)

    @property
    def full_name(self) -> str:
        """Get the full package name including source."""
        return f"{self.source}:{self.name}"

    def update_dependencies(self, dependencies: frozenset[Dependency]) -> Package:
        """Update package dependencies.

        Args:
            dependencies: New dependencies to add

        Returns:
            Self for method chaining

        """
        self.dependencies = self.dependencies.union(dependencies)
        return self

    def update_vulnerabilities(self, vulnerabilities: frozenset[Vulnerability]) -> Package:
        """Update package vulnerabilities.

        Args:
            vulnerabilities: New vulnerabilities to add

        Returns:
            Self for method chaining

        """
        self.vulnerabilities = self.vulnerabilities.union(vulnerabilities)
        return self

    @property
    def resolver(self) -> DependencyResolver:
        """Get the initial main resolver for this package.

        Other resolvers could have added dependencies and perform modifications over it.

        Returns:
            The resolver for this package's source

        """
        from .resolver import resolver_by_name  # noqa: PLC0415

        return resolver_by_name(self.source)

    @classmethod
    def from_string(cls, description: str) -> Package:
        """Create a package from a full name string.

        For example:
            ubuntu:libc6@2.31
            ubuntu:libc6@2.31[]
            ubuntu:libc6@2.31[ubuntu:somepkg@<0.1.0]
            ubuntu:libc6@2.31[ubuntu:somepkg@<0.1.0, ubuntu:otherpkg@=2.1.0]
            ubuntu,native:libc6@2.31[ubuntu:somepkg@<0.1.0, ubuntu:otherpkg@=2.1.0, ubuntu:libc@*]

        Args:
            description: Package description string

        Returns:
            New Package instance

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

    def __str__(self) -> str:
        """Get string representation of package."""
        dependencies = "[" + ",".join(map(str, sorted(self.dependencies))) + "]" if self.dependencies else ""
        return f"{self.source}:{self.name}@{self.version}" + dependencies

    def to_dependency(self) -> Dependency:
        """Convert package to a dependency."""
        return Dependency(
            package=self.name,
            semantic_version=self.resolver.parse_spec(f"={self.version}"),
            source=self.source,
        )

    def to_obj(self) -> dict[str, Any]:
        """Convert package to dictionary representation."""
        return {
            "source": self.source,
            "name": self.name,
            "version": str(self.version),
            "dependencies": {f"{dep.source}:{dep.package}": str(dep.semantic_version) for dep in self.dependencies},
            "vulnerabilities": [vuln.to_obj() for vuln in self.vulnerabilities],
        }

    def dumps(self) -> str:
        """Serialize package to JSON string."""
        return json.dumps(self.to_obj())

    def same_package(self, other: Package) -> bool:
        """Check if two packages are the same, but potentially different versions.

        Args:
            other: Package to compare with

        Returns:
            True if packages are the same (name and source)

        """
        return self.name == other.name and self.source == other.source

    def __eq__(self, other: object) -> bool:
        """Check equality with another package."""
        if isinstance(other, Package):
            return other.name == self.name and other.source == self.source and other.version == self.version
        return False

    def __lt__(self, other: object) -> bool:
        """Compare packages for sorting."""
        return isinstance(other, Package) and (self.name, self.source, self.version) < (
            other.name,
            other.source,
            other.version,
        )

    def __hash__(self) -> int:
        """Compute hash for package."""
        return hash((self.version, self.name, self.version))


class SourcePackage(Package):
    """A package extracted from source code rather than a package repository.

    It is a package that exists on disk, but not necessarily in a remote repository.
    """

    def __init__(  # noqa: PLR0913
        self,
        name: str,
        version: Version,
        source_repo: SourceRepository,
        source: str,
        dependencies: Iterable[Dependency] = (),
        vulnerabilities: Iterable[Vulnerability] = (),
    ) -> None:
        """Initialize a source package.

        Args:
            name: Package name
            version: Package version
            source_repo: Source repository
            source: Source resolver name
            dependencies: Package dependencies
            vulnerabilities: Known vulnerabilities

        """
        super().__init__(
            name=name,
            version=version,
            dependencies=dependencies,
            source=source,
            vulnerabilities=vulnerabilities,
        )
        self.source_repo: SourceRepository = source_repo

    def __str__(self) -> str:
        """Get string representation of source package."""
        return f"{super().__str__()}:{self.source_repo.path.absolute()!s}"
