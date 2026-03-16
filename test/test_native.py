from platform import machine
from unittest import TestCase

from it_depends.dependencies import Package, Version
from it_depends.native import get_native_dependencies


def arch_string() -> str:
    """Returns an architecture dependendent string for filenames
    Current support is only arm64/x86_64."""
    # TODO (hbrodin): Make more general. # noqa: TD003, FIX002
    return "aarch64" if machine() == "arm64" else "x86_64"


class TestNative(TestCase):
    def test_native(self) -> None:
        deps = {
            dep.package
            for dep in get_native_dependencies(Package(name="numpy", version=Version.coerce("2.2.6"), source="pip"))
        }
        arch = arch_string()
        expected = {
            f"/lib/{arch}-linux-gnu/libc.so.6",
            f"/lib/{arch}-linux-gnu/libm.so.6",
            f"/lib/{arch}-linux-gnu/libgcc_s.so.1",
            f"/lib/{arch}-linux-gnu/libpthread.so.0",
        }
        assert expected.issubset(deps), f"Missing deps: {expected - deps}\nGot: {deps}"
