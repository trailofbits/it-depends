"""Tests for PackageCache __contains__ and to_graph performance fixes."""

import gc
from collections.abc import Iterator
from unittest import TestCase

from it_depends.db import DBPackageCache
from it_depends.dependencies import (
    Dependency,
    DependencyResolver,
    InMemoryPackageCache,
    Package,
    PackageCache,
    ResolverAvailability,
    SimpleSpec,
    SourceRepository,
    Version,
    resolver_by_name,
    resolvers,
)


class TestPackageCacheContains(TestCase):
    """Tests for __contains__ overrides and to_graph correctness."""

    def setUp(self) -> None:
        """Set up test fixtures."""

        class UnusedResolver(DependencyResolver):
            name: str = "unknown"
            description: str = "Used for testing"

            def is_available(self) -> ResolverAvailability:
                return ResolverAvailability(is_available=False, reason="Unused resolver")

            def can_resolve_from_source(self, repo: SourceRepository) -> bool:  # noqa: ARG002
                return False

            def resolve_from_source(
                self,
                repo: SourceRepository,
                cache: PackageCache | None = None,
            ) -> None:
                raise NotImplementedError

            def resolve(self, dependency: Dependency) -> Iterator[Package]:
                raise NotImplementedError

        self.unknown = UnusedResolver
        del UnusedResolver

    def tearDown(self) -> None:
        """Clean up test fixtures."""
        del self.unknown
        resolvers.cache_clear()
        resolver_by_name.cache_clear()
        gc.collect()
        gc.collect()

    def _make_pkg(
        self,
        name: str = "foo",
        version: str = "1.0.0",
        dependencies: tuple[Dependency, ...] = (),
    ) -> Package:
        return Package(
            name=name,
            version=Version.coerce(version),
            source=self.unknown(),
            dependencies=dependencies,
        )

    def test_in_memory_contains_present(self) -> None:
        cache = InMemoryPackageCache()
        pkg = self._make_pkg()
        cache.add(pkg)
        assert pkg in cache

    def test_in_memory_contains_absent(self) -> None:
        cache = InMemoryPackageCache()
        pkg = self._make_pkg()
        assert pkg not in cache

    def test_in_memory_contains_wrong_version(self) -> None:
        cache = InMemoryPackageCache()
        cache.add(self._make_pkg(version="1.0.0"))
        assert self._make_pkg(version="2.0.0") not in cache

    def test_in_memory_contains_wrong_name(self) -> None:
        cache = InMemoryPackageCache()
        cache.add(self._make_pkg(name="foo"))
        assert self._make_pkg(name="bar") not in cache

    def test_in_memory_contains_non_package(self) -> None:
        cache = InMemoryPackageCache()
        assert "not a package" not in cache

    def test_db_contains_present(self) -> None:
        with DBPackageCache() as cache:
            pkg = self._make_pkg()
            cache.add(pkg)
            assert pkg in cache

    def test_db_contains_absent(self) -> None:
        with DBPackageCache() as cache:
            pkg = self._make_pkg()
            assert pkg not in cache

    def test_db_contains_wrong_version(self) -> None:
        with DBPackageCache() as cache:
            cache.add(self._make_pkg(version="1.0.0"))
            assert self._make_pkg(version="2.0.0") not in cache

    def test_db_contains_non_package(self) -> None:
        with DBPackageCache() as cache:
            assert "not a package" not in cache

    def test_to_graph_with_dependencies(self) -> None:
        cache = InMemoryPackageCache()
        dep = Dependency(
            package="bar",
            semantic_version=SimpleSpec(">=1.0.0"),
            source=self.unknown(),
        )
        pkg_a = self._make_pkg(name="foo", dependencies=(dep,))
        pkg_b = self._make_pkg(name="bar")
        cache.add(pkg_a)
        cache.add(pkg_b)

        expected_node_count = 2
        graph = cache.to_graph()
        nodes = list(graph)
        assert len(nodes) == expected_node_count
        assert pkg_a in nodes
        assert pkg_b in nodes

    def test_to_graph_empty_cache(self) -> None:
        cache = InMemoryPackageCache()
        graph = cache.to_graph()
        assert len(list(graph)) == 0
