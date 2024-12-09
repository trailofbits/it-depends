from appdirs import AppDirs
import sys

if sys.version_info < (3, 12):
    import pkg_resources

    def version() -> str:
        return pkg_resources.require("it-depends")[0].version

else:
    from importlib.metadata import version as meta_version

    def version() -> str:
        return meta_version("it-depends")


APP_DIRS = AppDirs("it-depends", "Trail of Bits")