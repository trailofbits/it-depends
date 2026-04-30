from unittest import TestCase
from unittest.mock import patch

from it_depends.dependencies import Dependency
from it_depends.ubuntu.resolver import UbuntuResolver, _clean_dep_version


class TestCleanDepVersion(TestCase):
    """Tests for _clean_dep_version helper."""

    def test_none_returns_star(self) -> None:
        assert _clean_dep_version(None) == "*"

    def test_simple_version_spec(self) -> None:
        assert _clean_dep_version(">= 2.29") == ">=2.29"

    def test_epoch_stripped(self) -> None:
        assert _clean_dep_version(">= 1:4.4.10") == ">=4.4.10"

    def test_tilde_stripped(self) -> None:
        assert _clean_dep_version(">= 3.0~~alpha1") == ">=3.0"
        assert _clean_dep_version(">= 1.15~beta1") == ">=1.15"

    def test_dash_stripped(self) -> None:
        assert _clean_dep_version(">= 4.4.10-10ubuntu4") == ">=4.4.10"

    def test_epoch_and_dash(self) -> None:
        assert _clean_dep_version("= 1:7.0.1-12") == "=7.0.1"

    def test_all_combined(self) -> None:
        assert _clean_dep_version(">= 1:4.4.10~alpha1-10ubuntu4") == ">=4.4.10"


class TestUbuntu(TestCase):
    def test_ubuntu(self) -> None:
        contents = """Package: dkms
Version: 3.0.11-1ubuntu13
Priority: optional
Section: admin
Origin: Ubuntu
Maintainer: Ubuntu Developers <ubuntu-devel-discuss@lists.ubuntu.com>
Original-Maintainer: Dynamic Kernel Module System Team <dkms@packages.debian.org>
Bugs: https://bugs.launchpad.net/ubuntu/+filebug
Installed-Size: 196 kB
Provides: dkms-autopkgtest (= 3.0.11-1ubuntu13)
Pre-Depends: lsb-release
Depends: kmod | kldutils, gcc, gcc-13, dpkg-dev, make | build-essential, patch
Recommends: fakeroot, sudo, linux-headers-generic | linux-headers-686-pae | linux-headers-amd64 | linux-headers
Suggests: menu, e2fsprogs
Homepage: https://github.com/dell/dkms
Download-Size: 51.5 kB
APT-Sources: http://ports.ubuntu.com/ubuntu-ports noble/main arm64 Packages
Description: Dynamic Kernel Module System (DKMS)
"""
        with patch("it_depends.ubuntu.resolver.run_command") as mock:
            mock.return_value = contents.encode()
            deps = tuple(UbuntuResolver().resolve(dependency=Dependency(package="dkms", source="ubuntu")))
            assert len(deps) == 1
            assert str(deps[0]) == (
                "ubuntu:dkms@3.0.11[ubuntu:build-essential@*,ubuntu:dpkg-dev@*,"
                "ubuntu:gcc-13@*,ubuntu:gcc@*,ubuntu:kldutils@*,ubuntu:kmod@*,ubuntu:make@*,ubuntu:patch@*]"
            )

    def test_ubuntu_epoch_dependency(self) -> None:
        """Epoch-prefixed dependency versions are parsed correctly."""
        contents = b"""Package: libcrypt1
Version: 1:4.4.27-1
Depends: libc6 (>= 1:2.25)
"""
        with patch("it_depends.ubuntu.resolver.run_command") as mock:
            mock.return_value = contents
            deps = tuple(UbuntuResolver().resolve(dependency=Dependency(package="libcrypt1", source="ubuntu")))
            assert len(deps) == 1
            dep_list = sorted(deps[0].dependencies, key=lambda d: d.package)
            assert len(dep_list) == 1
            assert dep_list[0].package == "libc6"
            assert str(dep_list[0].semantic_version) == ">=2.25"

    def test_ubuntu_tilde_dependency(self) -> None:
        """Tilde pre-release dependency versions are parsed correctly."""
        contents = b"""Package: libfoo
Version: 3.0.0-1
Depends: libbar (>= 3.0.0~~alpha1)
"""
        with patch("it_depends.ubuntu.resolver.run_command") as mock:
            mock.return_value = contents
            deps = tuple(UbuntuResolver().resolve(dependency=Dependency(package="libfoo", source="ubuntu")))
            assert len(deps) == 1
            dep_list = list(deps[0].dependencies)
            assert len(dep_list) == 1
            assert dep_list[0].package == "libbar"
            assert str(dep_list[0].semantic_version) == ">=3.0.0"

    def test_ubuntu_no_version_dependency(self) -> None:
        """Dependencies without version constraints default to *."""
        contents = b"""Package: mypkg
Version: 1.0.0-1
Depends: gcc, make
"""
        with patch("it_depends.ubuntu.resolver.run_command") as mock:
            mock.return_value = contents
            deps = tuple(UbuntuResolver().resolve(dependency=Dependency(package="mypkg", source="ubuntu")))
            assert len(deps) == 1
            for d in deps[0].dependencies:
                assert str(d.semantic_version) == "*"
