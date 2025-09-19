"""Version and configuration utilities for it-depends."""

import sys

from platformdirs import PlatformDirs

if sys.version_info < (3, 12):
    import pkg_resources

    def version() -> str:
        """Get version using pkg_resources for Python < 3.12."""
        return pkg_resources.require("it-depends")[0].version

else:
    from importlib.metadata import version as meta_version

    def version() -> str:
        """Get version using importlib.metadata for Python >= 3.12."""
        return meta_version("it-depends")


APP_DIRS = PlatformDirs("it-depends", "Trail of Bits")
