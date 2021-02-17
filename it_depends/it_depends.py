import pkg_resources


def version() -> str:
    return pkg_resources.require("it-depends")[0].version
