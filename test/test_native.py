from unittest import TestCase

from it_depends.dependencies import Package, Version
from it_depends.native import NativeResolver


class TestNative(TestCase):
    def test_native(self):
        deps = {dep.name for dep in NativeResolver.get_native_dependencies(Package(
            name="numpy",
            version=Version.coerce("1.19.4"),
            source="pip"
        ))}
        self.assertIn("libc6", deps)
        self.assertIn("libtinfo6", deps)
