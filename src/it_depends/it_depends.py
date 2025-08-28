import sys

from platformdirs import PlatformDirs

if sys.version_info < (3, 12):
    import pkg_resources

    def version() -> str:
        return pkg_resources.require("it-depends")[0].version

else:
    from importlib.metadata import version as meta_version

    def version() -> str:
        return meta_version("it-depends")


APP_DIRS = PlatformDirs("it-depends", "Trail of Bits")
