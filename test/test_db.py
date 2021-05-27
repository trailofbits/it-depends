from unittest import TestCase

from it_depends.db import DBPackageCache
from it_depends.dependencies import Dependency, DependencyResolver, Package, ResolverAvailability, SimpleSpec, Version


class UnusedResolver(DependencyResolver):
    name: str = "unknown"
    description: str = "Used for testing"

    def is_available(self) -> ResolverAvailability:
        return ResolverAvailability(False, "Unused resolver")

    def can_resolve_from_source(self, repo) -> bool:
        return False

    def resolve_from_source(self, repo, cache = None):
        raise NotImplementedError()


class TestDB(TestCase):
    def test_db(self):
        with DBPackageCache() as cache:
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
