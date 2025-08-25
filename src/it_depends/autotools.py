import os
import functools
import re
import itertools
import shutil
import subprocess
import logging
import tempfile
from typing import List, Optional, Tuple

from it_depends.ubuntu.apt import cached_file_to_package as file_to_package

from .dependencies import (
    Dependency,
    DependencyResolver,
    PackageCache,
    ResolverAvailability,
    SimpleSpec,
    SourcePackage,
    SourceRepository,
    Version,
)

logger = logging.getLogger(__name__)


class AutotoolsResolver(DependencyResolver):
    """This attempts to parse configure.ac in an autotool based repo.
    It supports the following macros:
        AC_INIT, AC_CHECK_HEADER, AC_CHECK_LIB, PKG_CHECK_MODULES

    BUGS:
        does not handle boost deps
        assumes ubuntu host
    """

    name = "autotools"
    description = "classifies the dependencies of native/autotools packages parsing configure.ac"

    def is_available(self) -> ResolverAvailability:
        if shutil.which("autoconf") is None:
            return ResolverAvailability(
                False,
                "`autoconf` does not appear to be installed! "
                "Make sure it is installed and in the PATH.",
            )
        return ResolverAvailability(True)

    def can_resolve_from_source(self, repo: SourceRepository) -> bool:
        return bool(self.is_available()) and (repo.path / "configure.ac").exists()

    @staticmethod
    def _ac_check_header(header_file, file_to_package_cache=None):
        """
        Macro: AC_CHECK_HEADER
        Checks if the system header file header-file is compilable.
        https://www.gnu.org/software/autoconf/manual/autoconf-2.67/html_node/Generic-Headers.html
        """
        logger.info(f"AC_CHECK_HEADER {header_file}")
        package_name = file_to_package(
            f"{re.escape(header_file)}", file_to_package_cache=file_to_package_cache
        )
        return Dependency(package=package_name, semantic_version=SimpleSpec("*"), source="ubuntu")

    @staticmethod
    def _ac_check_lib(function, file_to_package_cache=None):
        """
        Macro: AC_CHECK_LIB
        Checks for the presence of certain C, C++, or Fortran library archive files.
        https://www.gnu.org/software/autoconf/manual/autoconf-2.67/html_node/Libraries.html#Libraries
        """
        lib_file, function_name = function.split(".")
        logger.info(f"AC_CHECK_LIB {lib_file}")
        package_name = file_to_package(
            f"lib{re.escape(lib_file)}(.a|.so)",
            file_to_package_cache=file_to_package_cache,
        )
        return Dependency(package=package_name, semantic_version=SimpleSpec("*"), source="ubuntu")

    @staticmethod
    def _pkg_check_modules(module_name, version=None, file_to_package_cache=None):
        """
        Macro: PKG_CHECK_MODULES
        The main interface between autoconf and pkg-config.
        Provides a very basic and easy way to check for the presence of a
        given package in the system.
        """
        if not version:
            version = "*"
        module_file = re.escape(module_name + ".pc")
        logger.info(f"PKG_CHECK_MODULES {module_file}, {version}")
        package_name = file_to_package(module_file, file_to_package_cache=file_to_package_cache)
        return Dependency(
            package=package_name, semantic_version=SimpleSpec(version), source="ubuntu"
        )

    @staticmethod
    @functools.lru_cache(maxsize=128)
    def _replace_variables(token: str, configure: str):
        """
        Search all variable occurrences in token and then try to find
        bindings for them in the configure script.
        """
        if "$" not in token:
            return token
        variable_list = re.findall(r"\$([a-zA-Z_0-9]+)|\${([_a-zA-Z0-9]+)}", token)
        variables = set(
            var for var in itertools.chain(*variable_list) if var
        )  # remove dups and empty
        for var in variables:
            logger.info(f"Trying to find bindings for {var} in configure")

            # This tries to find a single assign to the variable in question
            # ... var= "SOMETHING"
            # We ignore the fact thst variables could also appear in other constructs
            # For example:
            #    for var in THIS THAT ;
            # TODO/CHALLENGE Merge this two \/
            solutions = re.findall(f'{var}=\\s*"([^"]*)"', configure)
            solutions += re.findall(f"{var}=\\s*'([^']*)'", configure)
            if len(solutions) > 1:
                logger.warning(f"Found several solutions for {var}: {solutions}")
            if len(solutions) == 0:
                logger.warning(f"No solution found for binding {var}")
                continue
            logger.info(f"Found a solution {solutions}")
            sol = (
                solutions
                + [
                    None,
                ]
            )[0]
            if sol is not None:
                token = token.replace(f"${var}", sol).replace(f"${{{var}}}", sol)
        if "$" in token:
            raise ValueError(f"Could not find a binding for variable/s in {token}")
        return token

    def resolve_from_source(
        self, repo: SourceRepository, cache: Optional[PackageCache] = None
    ) -> Optional[SourcePackage]:
        if not self.can_resolve_from_source(repo):
            return None
        logger.info(f"Getting dependencies for autotool repo {repo.path.absolute()}")
        with tempfile.NamedTemporaryFile() as tmp:
            # builds a temporary copy of configure.ac containing aclocal env
            subprocess.check_output(("aclocal", f"--output={tmp.name}"), cwd=repo.path)
            with open(tmp.name, "ab") as tmp2:
                with open(repo.path / "configure.ac", "rb") as conf:
                    tmp2.write(conf.read())

            trace = subprocess.check_output(
                (
                    "autoconf",
                    "-t",
                    "AC_CHECK_HEADER:$n:$1",
                    "-t",
                    "AC_CHECK_LIB:$n:$1.$2",
                    "-t",
                    "PKG_CHECK_MODULES:$n:$2",
                    "-t",
                    "PKG_CHECK_MODULES_STATIC:$n",
                    tmp.name,
                ),
                cwd=repo.path,
            ).decode("utf8")
            configure = subprocess.check_output(["autoconf", tmp.name], cwd=repo.path).decode(
                "utf8"
            )

        file_to_package_cache: List[Tuple[str]] = []
        deps = []
        for macro in trace.split("\n"):
            logger.debug(f"Handling: {macro}")
            macro, *arguments = macro.split(":")
            try:
                arguments = tuple(self._replace_variables(arg, configure) for arg in arguments)  # type: ignore
            except Exception as e:
                logger.info(str(e))
                continue
            try:
                if macro == "AC_CHECK_HEADER":
                    deps.append(
                        self._ac_check_header(
                            header_file=arguments[0],
                            file_to_package_cache=file_to_package_cache,
                        )
                    )
                elif macro == "AC_CHECK_LIB":
                    deps.append(
                        self._ac_check_lib(
                            function=arguments[0],
                            file_to_package_cache=file_to_package_cache,
                        )
                    )
                elif macro == "PKG_CHECK_MODULES":
                    module_name, *version = arguments[0].split(" ")
                    deps.append(
                        self._pkg_check_modules(
                            module_name=module_name,
                            version="".join(version),
                            file_to_package_cache=file_to_package_cache,
                        )
                    )
                else:
                    logger.error("Macro not supported %r", macro)
            except Exception as e:
                logger.error(str(e))
                continue

        """
        # Identity of this package.
        PACKAGE_NAME='Bitcoin Core'
        PACKAGE_TARNAME='bitcoin'
        PACKAGE_VERSION='21.99.0'
        PACKAGE_STRING='Bitcoin Core 21.99.0'
        PACKAGE_BUGREPORT='https://github.com/bitcoin/bitcoin/issues'
        PACKAGE_URL='https://bitcoincore.org/"""
        try:
            package_name = self._replace_variables("$PACKAGE_NAME", configure)
        except ValueError as e:
            logger.error(str(e))
            package_name = os.path.basename(repo.path)

        try:
            package_version = self._replace_variables("$PACKAGE_VERSION", configure)
        except ValueError as e:
            logger.error(str(e))
            package_version = "0.0.0"

        return SourcePackage(
            name=package_name,
            version=Version.coerce(package_version),
            source=self.name,
            dependencies=deps,
            source_repo=repo,
        )
