import gc
from collections.abc import Iterator
from unittest import TestCase

import pytest

from it_depends.db import DBPackageCache
from it_depends.dependencies import (
    Dependency,
    DependencyResolver,
    Package,
    ResolverAvailability,
    SimpleSpec,
    SourceRepository,
    Version,
    resolver_by_name,
    resolvers,
)


class TestDB(TestCase):
    def setUp(self) -> None:
        class UnusedResolver(DependencyResolver):
            name: str = "unknown"
            description: str = "Used for testing"

            def is_available(self) -> ResolverAvailability:
                return ResolverAvailability(is_available=False, reason="Unused resolver")

            def can_resolve_from_source(self, repo: SourceRepository) -> bool:  # noqa: ARG002
                return False

            def resolve_from_source(self, repo: SourceRepository, cache=None) -> None:  # noqa: ANN001
                raise NotImplementedError

            def resolve(self, dependency: Dependency) -> Iterator[Package]:
                raise NotImplementedError

        self.unknown = UnusedResolver
        del UnusedResolver

    def tearDown(self) -> None:
        del self.unknown
        resolvers.cache_clear()
        resolver_by_name.cache_clear()

        gc.collect()
        gc.collect()
        #  remove Unused resolver from Resolvers global set

    def test_db(self) -> None:
        with DBPackageCache() as cache:
            UnusedResolver = self.unknown  # noqa: N806
            pkg = Package(
                name="package",
                version=Version.coerce("1.0.0"),
                source=UnusedResolver(),
                dependencies=(Dependency(package="dep", semantic_version=SimpleSpec(">3.0"), source=UnusedResolver()),),
            )
            cache.add(pkg)
            assert pkg in cache
            assert len(cache) == 1
            # re-adding the package should be a NO-OP
            cache.add(pkg)
            assert len(cache) == 1
            # try adding the package again, but with fewer dependencies:
            smaller_pkg = Package(name="package", version=Version.coerce("1.0.0"), source=UnusedResolver())
            with pytest.raises(ValueError, match="Package .* has already been resolved with more dependencies"):
                cache.add(smaller_pkg)
