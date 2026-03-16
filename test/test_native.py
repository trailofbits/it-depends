from platform import machine
from unittest import TestCase

from it_depends.dependencies import Package, Version
from it_depends.native import STRACE_LIBRARY_REGEX, get_native_dependencies


def arch_string() -> str:
    """Returns an architecture dependendent string for filenames
    Current support is only arm64/x86_64."""
    # TODO (hbrodin): Make more general. # noqa: TD003, FIX002
    return "aarch64" if machine() == "arm64" else "x86_64"


class TestStraceLibraryRegex(TestCase):
    """Unit tests for STRACE_LIBRARY_REGEX pattern matching."""

    def test_openat_basic(self) -> None:
        line = 'openat(AT_FDCWD, "/lib/x86_64-linux-gnu/libc.so.6", O_RDONLY|O_CLOEXEC) = 4'
        m = STRACE_LIBRARY_REGEX.match(line)
        assert m is not None
        assert m.group(3) == "/lib/x86_64-linux-gnu/libc.so.6"
        assert m.group(4) == "/lib/x86_64-linux-gnu/"
        assert m.group(5) == "libc"
        assert m.group(7) == "6"

    def test_openat_with_pid_prefix(self) -> None:
        line = '[pid  123] openat(AT_FDCWD, "/lib/x86_64-linux-gnu/libpthread.so.0", O_RDONLY) = 5'
        m = STRACE_LIBRARY_REGEX.match(line)
        assert m is not None
        assert m.group(1) == "[pid  123] "
        assert m.group(3) == "/lib/x86_64-linux-gnu/libpthread.so.0"

    def test_openat_no_version_suffix(self) -> None:
        line = 'openat(AT_FDCWD, "/usr/lib/libfoo.so", O_RDONLY) = 3'
        m = STRACE_LIBRARY_REGEX.match(line)
        assert m is not None
        assert m.group(3) == "/usr/lib/libfoo.so"
        assert m.group(5) == "libfoo"
        assert m.group(6) is None
        assert m.group(7) is None

    def test_openat_multipart_version(self) -> None:
        line = 'openat(AT_FDCWD, "/lib/libz.so.1.2.11", O_RDONLY) = 3'
        m = STRACE_LIBRARY_REGEX.match(line)
        assert m is not None
        assert m.group(3) == "/lib/libz.so.1.2.11"
        assert m.group(7) == "1.2.11"

    def test_open_does_not_match(self) -> None:
        """Plain open() puts the path as arg 1, so the regex cannot match it."""
        line = 'open("/lib/x86_64-linux-gnu/libc.so.6", O_RDONLY) = 4'
        assert STRACE_LIBRARY_REGEX.match(line) is None

    def test_pid_prefix_open_does_not_match(self) -> None:
        """Plain open() with pid prefix also does not match."""
        line = '[pid  456] open("/lib/x86_64-linux-gnu/libc.so.6", O_RDONLY) = 4'
        assert STRACE_LIBRARY_REGEX.match(line) is None

    def test_non_library_path_does_not_match(self) -> None:
        line = 'openat(AT_FDCWD, "/etc/passwd", O_RDONLY) = 3'
        assert STRACE_LIBRARY_REGEX.match(line) is None

    def test_ld_so_cache_matches_but_filtered_by_caller(self) -> None:
        """The regex matches ld.so.cache; get_dependencies filters it out."""
        line = 'openat(AT_FDCWD, "/etc/ld.so.cache", O_RDONLY|O_CLOEXEC) = 3'
        m = STRACE_LIBRARY_REGEX.match(line)
        assert m is not None
        assert m.group(3) == "/etc/ld.so.cache"


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
