from unittest import TestCase
from unittest.mock import patch
from it_depends.dependencies import Dependency
from it_depends.ubuntu.resolver import UbuntuResolver


class TestUbuntu(TestCase):
    def test_ubuntu(self):
        contents = """Package: dkms
Version: 2.8.1-5ubuntu2
Priority: optional
Section: admin
Origin: Ubuntu
Maintainer: Ubuntu Developers <ubuntu-devel-discuss@lists.ubuntu.com>
Original-Maintainer: Dynamic Kernel Modules Support Team <dkms@packages.debian.org>
Bugs: https://bugs.launchpad.net/ubuntu/+filebug
Installed-Size: 296 kB
Pre-Depends: lsb-release
Depends: kmod | kldutils, gcc | c-compiler, dpkg-dev, make | build-essential, coreutils (>= 7.3), patch, dctrl-tools
Recommends: fakeroot, sudo, linux-headers-686-pae | linux-headers-amd64 | linux-headers-generic | linux-headers
Suggests: menu, e2fsprogs
Breaks: shim-signed (<< 1.34~)
Homepage: https://github.com/dell-oss/dkms
Download-Size: 66,8 kB
APT-Manual-Installed: no
APT-Sources: http://ar.archive.ubuntu.com/ubuntu focal-updates/main amd64 Packages
Description: Dynamic Kernel Module Support Framework
 DKMS is a framework designed to allow individual kernel modules to be upgraded
 without changing the whole kernel. It is also very easy to rebuild modules as
 you upgrade kernels.

Package: dkms
Version: 2.8.1-5ubuntu1
Priority: optional
Section: admin
Origin: Ubuntu
Maintainer: Ubuntu Developers <ubuntu-devel-discuss@lists.ubuntu.com>
Original-Maintainer: Dynamic Kernel Modules Support Team <dkms@packages.debian.org>
Bugs: https://bugs.launchpad.net/ubuntu/+filebug
Installed-Size: 296 kB
Pre-Depends: lsb-release
Depends: kmod | kldutils, gcc | c-compiler, dpkg-dev, make | build-essential, coreutils (>= 7.5), patch
Recommends: fakeroot, sudo, linux-headers-686-pae | linux-headers-amd64 | linux-headers-generic | linux-headers
Suggests: menu, e2fsprogs
Breaks: shim-signed (<< 1.34~)
Homepage: https://github.com/dell-oss/dkms
Download-Size: 66,6 kB
APT-Sources: http://ar.archive.ubuntu.com/ubuntu focal/main amd64 Packages
Description: Dynamic Kernel Module Support Framework
 DKMS is a framework designed to allow individual kernel modules to be upgraded
 without changing the whole kernel. It is also very easy to rebuild modules as
 you upgrade kernels.

"""
        with patch('it_depends.ubuntu.docker.run_command') as mock:
            mock.return_value = contents.encode()
            deps = tuple(UbuntuResolver().resolve(dependency=Dependency(package="dkms", source="ubuntu")))
            self.assertEqual(len(deps), 1)
            self.assertEqual(str(deps[0]), 'ubuntu:dkms@2.8.1[ubuntu:build-essential@*,ubuntu:c-compiler@*,'
                                           'ubuntu:coreutils@>=7.4,ubuntu:dctrl-tools@*,ubuntu:dpkg-dev@*,'
                                           'ubuntu:gcc@*,ubuntu:kldutils@*,ubuntu:kmod@*,ubuntu:make@*,ubuntu:patch@*]')
