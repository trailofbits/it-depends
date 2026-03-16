from unittest import TestCase
from unittest.mock import patch

from it_depends.dependencies import Dependency
from it_depends.ubuntu.resolver import UbuntuResolver


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
