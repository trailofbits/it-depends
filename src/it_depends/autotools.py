"""Autotools dependency resolution."""

from __future__ import annotations

import functools
import itertools
import logging
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from .dependencies import (
    Dependency,
    DependencyResolver,
    ResolverAvailability,
    SimpleSpec,
    SourcePackage,
    SourceRepository,
    Version,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

    from .models import Package

from .ubuntu.apt import (
    cached_file_to_package as file_to_package,
)
from .ubuntu.apt import (
    make_include_query,
    make_library_query,
    make_pkg_config_query,
)

logger = logging.getLogger(__name__)


class AutotoolsResolver(DependencyResolver):
    """Parse configure.ac in an autotool based repo.

    It supports the following macros:
        AC_INIT, AC_CHECK_HEADER, AC_CHECK_LIB, PKG_CHECK_MODULES

    BUGS:
        does not handle boost deps
        assumes ubuntu host
    """

    name = "autotools"
    description = "classifies the dependencies of native/autotools packages parsing configure.ac"

    def is_available(self) -> ResolverAvailability:
        """Check if autotools resolver is available."""
        if shutil.which("autoconf") is None:
            return ResolverAvailability(
                is_available=False,
                reason="`autoconf` does not appear to be installed! Make sure it is installed and in the PATH.",
            )
        return ResolverAvailability(is_available=True)

    def can_resolve_from_source(self, repo: SourceRepository) -> bool:
        """Check if resolver can resolve from source repository."""
        return bool(self.is_available()) and (repo.path / "configure.ac").exists()

    @staticmethod
    def _ac_check_header(header_file: str, file_to_package_cache: list[tuple[str, str]] | None = None) -> Dependency:
        """Macro: AC_CHECK_HEADER.

        Checks if the system header file header-file is compilable.
        https://www.gnu.org/software/autoconf/manual/autoconf-2.67/html_node/Generic-Headers.html
        """
        logger.info("AC_CHECK_HEADER %s", header_file)
        query = make_include_query([header_file])
        package_name = file_to_package(query, file_to_package_cache=file_to_package_cache)
        return Dependency(package=package_name, semantic_version=SimpleSpec("*"), source="ubuntu")

    @staticmethod
    def _ac_check_lib(function: str, file_to_package_cache: list[tuple[str, str]] | None = None) -> Dependency:
        """Macro: AC_CHECK_LIB.

        Checks for the presence of certain C, C++, or Fortran library archive files.
        https://www.gnu.org/software/autoconf/manual/autoconf-2.67/html_node/Libraries.html#Libraries
        """
        lib_file, _ = function.split(".")
        logger.info("AC_CHECK_LIB %s", lib_file)
        query = make_library_query([lib_file])
        package_name = file_to_package(query, file_to_package_cache=file_to_package_cache)
        return Dependency(package=package_name, semantic_version=SimpleSpec("*"), source="ubuntu")

    @staticmethod
    def _pkg_check_modules(
        module_name: str, version: str | None = None, file_to_package_cache: list[tuple[str, str]] | None = None
    ) -> Dependency:
        """Macro: PKG_CHECK_MODULES.

        The main interface between autoconf and pkg-config.
        Provides a very basic and easy way to check for the presence of a
        given package in the system.
        """
        if not version:
            version = "*"
        logger.info("PKG_CHECK_MODULES %s.pc, %s", module_name, version)
        query = make_pkg_config_query([module_name])
        package_name = file_to_package(query, file_to_package_cache=file_to_package_cache)
        return Dependency(package=package_name, semantic_version=SimpleSpec(version), source="ubuntu")

    @staticmethod
    @functools.lru_cache(maxsize=128)
    def _replace_variables(token: str, configure: str) -> str:
        """Search all variable occurrences in token and then try to find bindings for them in the configure script."""
        if "$" not in token:
            return token
        variable_list = re.findall(r"\$([a-zA-Z_0-9]+)|\${([_a-zA-Z0-9]+)}", token)
        variables = {var for var in itertools.chain(*variable_list) if var}  # remove dups and empty
        for var in variables:
            logger.info("Trying to find bindings for %s in configure", var)

            # This tries to find a single assign to the variable in question
            # ... var= "SOMETHING"
            # We ignore the fact thst variables could also appear in other constructs
            # For example:
            #    for var in THIS THAT ;
            # TODO(@evandowning): Merge this two \/ # noqa: TD003, FIX002
            solutions = re.findall(f'{var}=\\s*"([^"]*)"', configure)
            solutions += re.findall(f"{var}=\\s*'([^']*)'", configure)
            if len(solutions) > 1:
                logger.warning("Found several solutions for %s: %s", var, solutions)
            if len(solutions) == 0:
                logger.warning("No solution found for binding %s", var)
                continue
            logger.info("Found a solution %s", solutions)
            sol = [*solutions, None][0]
            if sol is not None:
                token = token.replace(f"${var}", sol).replace(f"${{{var}}}", sol)
        if "$" in token:
            msg = f"Could not find a binding for variable/s in {token}"
            raise ValueError(msg)
        return token

    def resolve(self, dependency: Dependency) -> Iterator[Package]:  # noqa: ARG002
        """Resolve a dependency to packages."""
        return NotImplementedError  # type: ignore[return-value]

    def resolve_from_source(
        self,
        repo: SourceRepository,
        cache: object | None = None,  # noqa: ARG002
    ) -> SourcePackage | None:
        """Resolve dependencies from source repository."""
        if not self.can_resolve_from_source(repo):
            return None
        logger.info("Getting dependencies for autotool repo %s", repo.path.absolute())
        with tempfile.NamedTemporaryFile() as tmp:
            # builds a temporary copy of configure.ac containing aclocal env
            subprocess.check_output(("aclocal", f"--output={tmp.name}"), cwd=repo.path)  # noqa: S603
            with Path(tmp.name).open("ab") as tmp2, (repo.path / "configure.ac").open("rb") as conf:
                tmp2.write(conf.read())

            trace = subprocess.check_output(  # noqa: S603
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
            configure = subprocess.check_output(["autoconf", tmp.name], cwd=repo.path).decode("utf8")  # noqa: S603, S607

        file_to_package_cache: list[tuple[str, str]] = []
        deps = []
        for macro_line in trace.split("\n"):
            logger.debug("Handling: %s", macro_line)
            macro, *arguments = macro_line.split(":")
            try:
                arguments = tuple(self._replace_variables(arg, configure) for arg in arguments)  # type: ignore[assignment]
            except Exception:
                logger.exception("Error replacing variables")
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
            except Exception:
                logger.exception("Error processing macro %s", macro)
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
        except ValueError:
            logger.exception("Error getting package name")
            package_name = repo.path.name

        try:
            package_version = self._replace_variables("$PACKAGE_VERSION", configure)
        except ValueError:
            logger.exception("Error getting package version")
            package_version = "0.0.0"

        return SourcePackage(
            name=package_name,
            version=Version.coerce(package_version),
            source=self.name,
            dependencies=deps,
            source_repo=repo,
        )
