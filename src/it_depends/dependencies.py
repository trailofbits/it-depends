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
from typing import Dict, List, Optional, Set, Tuple, Union

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
    # Models
    "Vulnerability",
    "Dependency",
    "AliasedDependency",
    "Package",
    "SourcePackage",
    # Repository
    "SourceRepository",
    # Graph
    "DependencyGraph",
    # Cache
    "PackageCache",
    "InMemoryPackageCache",
    "PackageRepository",
    # Resolver
    "DependencyResolver",
    "ResolverAvailability",
    "DockerSetup",
    "resolvers",
    "resolver_by_name",
    "is_known_resolver",
    # Resolution
    "resolve",
    "resolve_sbom",
    # Types and utilities (for backward compatibility)
    "Dict",
    "List",
    "Tuple",
    "Set",
    "Optional",
    "Union",
    "Iterable",
    "Iterator",
    "SimpleSpec",
    "Version",
    "SemanticVersion",
]
