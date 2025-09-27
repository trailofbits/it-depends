"""Dependencies module - provides a unified interface to all dependency-related functionality.

This module has been modularized into several focused modules:
- models: Core data classes (Vulnerability, Dependency, Package, SourcePackage)
- repository: Source repository handling
- graph: Dependency graph functionality
- cache: Package caching system
- resolver: Base resolver classes and utilities
- resolution: Core resolution logic

For backward compatibility, all classes and functions are re-exported from this module.
"""

# Re-export all the core classes and functions for backward compatibility
# Re-export commonly used types and classes for backward compatibility
from collections.abc import Iterable, Iterator

from semantic_version import SimpleSpec, Version
from semantic_version.base import BaseSpec as SemanticVersion

from .cache import (
    InMemoryPackageCache,
    PackageCache,
    PackageRepository,
)
from .graph import DependencyGraph
from .models import (
    AliasedDependency,
    Dependency,
    Package,
    SourcePackage,
    Vulnerability,
)
from .repository import SourceRepository
from .resolution import resolve, resolve_sbom
from .resolver import (
    DependencyResolver,
    DockerSetup,
    ResolverAvailability,
    is_known_resolver,
    resolver_by_name,
    resolvers,
)

# Keep the __all__ for explicit imports
__all__ = [
    "AliasedDependency",
    "Dependency",
    "DependencyGraph",
    "DependencyResolver",
    "DockerSetup",
    "InMemoryPackageCache",
    "Iterable",
    "Iterator",
    "Package",
    "PackageCache",
    "PackageRepository",
    "ResolverAvailability",
    "SemanticVersion",
    "SimpleSpec",
    "SourcePackage",
    "SourceRepository",
    "Version",
    "Vulnerability",
    "is_known_resolver",
    "resolve",
    "resolve_sbom",
    "resolver_by_name",
    "resolvers",
]
