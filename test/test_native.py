from platform import machine
from unittest import TestCase

from it_depends.dependencies import Package, Version
from it_depends.native import get_native_dependencies


def arch_string() -> str:
    """Returns an architecture dependendent string for filenames
    Current support is only arm64/x86_64."""
    # TODO (hbrodin): Make more general.
    return "aarch64" if machine() == "arm64" else "x86_64"


class TestNative(TestCase):
    def test_native(self):
        deps = {dep.package for dep in get_native_dependencies(Package(
            name="numpy",
            version=Version.coerce("1.19.4"),
            source="pip"
        ))}
        arch = arch_string()
        self.assertEqual({
            f'/lib/{arch}-linux-gnu/libtinfo.so.6', f'/lib/{arch}-linux-gnu/libnss_files.so.2',
            f'/lib/{arch}-linux-gnu/libc.so.6', f'/lib/{arch}-linux-gnu/libdl.so.2'
        }, deps)
