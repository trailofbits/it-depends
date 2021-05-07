from unittest import TestCase

from it_depends.db import DBPackageCache
from it_depends.dependencies import Dependency, Package, SimpleSpec, Version, UnusedClassifier

class TestDB(TestCase):
    def test_db(self):
        with DBPackageCache() as cache:
            pkg = Package(name="package", version=Version.coerce("1.0.0"), source=UnusedClassifier(),
                          dependencies=(Dependency(package="dep", semantic_version=SimpleSpec(">3.0"), source=UnknownClassifier()),))
            cache.add(pkg)
            self.assertIn(pkg, cache)
            self.assertEqual(len(cache), 1)
            # re-adding the package should be a NO-OP
            cache.add(pkg)
            self.assertEqual(len(cache), 1)
            # try adding the package again, but with fewer dependencies:
            smaller_pkg = Package(name="package", version=Version.coerce("1.0.0"), source=UnusedClassifier())
            self.assertRaises(ValueError, cache.add, smaller_pkg)
