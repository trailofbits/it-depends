from unittest import TestCase

from it_depends.dependencies import Package, Version
from it_depends.native import get_native_dependencies


class TestNative(TestCase):
    def test_native(self):
        deps = {dep.package for dep in get_native_dependencies(Package(
            name="numpy",
            version=Version.coerce("1.19.4"),
            source="pip"
        ))}
        self.assertEqual({
            '/lib/x86_64-linux-gnu/libtinfo.so.6', '/lib/x86_64-linux-gnu/libnss_files.so.2',
            '/lib/x86_64-linux-gnu/libc.so.6', '/lib/x86_64-linux-gnu/libdl.so.2'
        }, deps)
