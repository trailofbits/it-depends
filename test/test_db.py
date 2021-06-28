from unittest import TestCase

from it_depends.db import DBPackageCache
from it_depends.dependencies import Dependency, DependencyResolver, Package, ResolverAvailability, SimpleSpec, Version, resolvers, resolver_by_name


class TestDB(TestCase):
    def setUp(self) -> None:
        class UnusedResolver(DependencyResolver):
            name: str = "unknown"
            description: str = "Used for testing"

            def is_available(self) -> ResolverAvailability:
                return ResolverAvailability(False, "Unused resolver")

            def can_resolve_from_source(self, repo) -> bool:
                return False

            def resolve_from_source(self, repo, cache=None):
                raise NotImplementedError()

        self.unknown = UnusedResolver
        del UnusedResolver

    def tearDown(self) -> None:
        del self.unknown
        resolvers.cache_clear()
        resolver_by_name.cache_clear()
        import gc
        gc.collect()
        gc.collect()
        #  remove Unused resolver from Resolvers global set

    def test_db(self):
        with DBPackageCache() as cache:
            UnusedResolver = self.unknown
            pkg = Package(name="package", version=Version.coerce("1.0.0"), source=UnusedResolver(),
                          dependencies=(Dependency(package="dep", semantic_version=SimpleSpec(">3.0"),
                                                   source=UnusedResolver()),))
            cache.add(pkg)
            self.assertIn(pkg, cache)
            self.assertEqual(len(cache), 1)
            # re-adding the package should be a NO-OP
            cache.add(pkg)
            self.assertEqual(len(cache), 1)
            # try adding the package again, but with fewer dependencies:
            smaller_pkg = Package(name="package", version=Version.coerce("1.0.0"), source=UnusedResolver())
            self.assertRaises(ValueError, cache.add, smaller_pkg)

