from appdirs import AppDirs
import pkg_resources


APP_DIRS = AppDirs("it-depends", "Trail of Bits")

def version() -> str:
    return pkg_resources.require("it-depends")[0].version
