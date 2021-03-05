import functools
import urllib.request
import gzip
import re
import itertools
import os
from os import chdir, getcwd
from pathlib import Path
import shutil
import subprocess
from typing import Iterable, Optional
from semantic_version.base import Always, BaseSpec
import logging

from .dependencies import (
    ClassifierAvailability, Dependency, DependencyClassifier, DependencyResolver, Package, PackageCache, SimpleSpec,
    Version
)

logger = logging.getLogger(__name__)


@BaseSpec.register_syntax
class AutoSpec(SimpleSpec):
    SYNTAX = 'autotools'

    class Parser(SimpleSpec.Parser):
        @classmethod
        def parse(cls, expression):
            blocks = [b.strip() for b in expression.split(',')]
            clause = Always()
            for block in blocks:
                if not cls.NAIVE_SPEC.match(block):
                    raise ValueError("Invalid simple block %r" % block)
                    clause &= cls.parse_block(block)

            return clause

    def __str__(self):
        # remove the whitespace to canonicalize the spec
        return ",".join(b.strip() for b in self.expression.split(','))


class AutotoolsClassifier(DependencyClassifier):
    """ This attempts to parse configure.ac in an autotool based repo.
    It supports the following macros:
        AC_INIT, AC_CHECK_HEADER, AC_CHECK_LIB, PKG_CHECK_MODULES

    BUGS:
        does not handle boost deps
        assumes ubuntu host
    """
    name = "autotools"
    description = "classifies the dependencies of native/autotools packages parsing configure.ac"

    def is_available(self) -> ClassifierAvailability:
        if shutil.which("autoconf") is None:
            return ClassifierAvailability(False, "`autoconf` does not appear to be installed! "
                                                 "Make sure it is installed and in the PATH.")
        return ClassifierAvailability(True)

    def can_classify(self, path: str) -> bool:
        return (Path(path) / "configure.ac").exists()

    @staticmethod
    @functools.lru_cache(maxsize=128)
    def _file_to_package_contents(filename, arch="amd64"):
        """
        Downloads and uses apt-file database directly
        # http://security.ubuntu.com/ubuntu/dists/focal-security/Contents-amd64.gz
        # http://security.ubuntu.com/ubuntu/dists/focal-security/Contents-i386.gz
        """
        if arch not in ("amd64", "i386"):
            raise ValueError("Only amd64 and i386 supported")
        db = {}
        selected = None

        #TODO find better location https://pypi.org/project/appdirs/?
        dbfile = os.path.join(os.path.dirname(__file__), f"Contents-{arch}.gz")

        if not os.path.exists(dbfile):
            urllib.request.urlretrieve(f"http://security.ubuntu.com/ubuntu/dists/focal-security/Contents-{arch}.gz", dbfile)
        regex = re.compile(filename)
        with gzip.open(dbfile, "rt") as contents:
            for line in contents.readlines():
                line = line[:-1].replace("    ", ",").replace("\t", "")
                filename_i, *packages_i = line.split(",")
                if regex.match(filename_i):
                    for package_i in packages_i:
                        db[filename_i] = package_i
                        if selected is None or len(selected[0]) > len(filename_i):
                            selected = filename_i, package_i
        if selected:
            logger.info(f"Found {len(db)} matching packages for {filename}. Choosing {selected[1]}")

    @staticmethod
    @functools.lru_cache(maxsize=128)
    def _file_to_package_apt_file(filename, arch="amd64"):
        if arch not in ("amd64", "i386"):
            raise ValueError("Only amd64 and i386 supported")
        contents = subprocess.run(["apt-file", "-x", "search", filename], stdout=subprocess.PIPE).stdout.decode("utf8")
        db = {}
        selected = None
        for line in contents.split("\n"):
            if not line:
                continue
            package_i, filename_i = line.split(": ")
            db[filename_i] = package_i
            if selected is None or len(selected[0]) > len(filename_i):
                selected = filename_i, package_i

        if selected:
            logger.info(f"Found {len(db)} matching packages for {filename}. Choosing {selected[1]}")
        return selected[1]

    @staticmethod
    @functools.lru_cache(maxsize=128)
    def _file_to_package(filename, arch="amd64"):
        try:
            package_name = AutotoolsClassifier._file_to_package_apt_file(filename, arch=arch)
        except Exception as e:
            print (f"Exception using apt-file, retrying by hand... {e}")
            package_name = AutotoolsClassifier._file_to_package_contents(filename, arch=arch)
        if package_name is None:
            raise ValueError(f"Could not find a package for file expression {filename}")

        return package_name

    @staticmethod
    @functools.lru_cache(maxsize=128)
    def _ac_check_header(header_file):
        """
        Macro: AC_CHECK_HEADER
        Checks if the system header file header-file is compilable.
        https://www.gnu.org/software/autoconf/manual/autoconf-2.67/html_node/Generic-Headers.html
        """
        logger.info(f"AC_CHECK_HEADER {header_file}")
        package_name=AutotoolsClassifier._file_to_package(f"{header_file}$")
        return Dependency(package=package_name,
                          semantic_version=SimpleSpec("*"),
                          )

    @staticmethod
    @functools.lru_cache(maxsize=128)
    def _ac_check_lib(function):
        """
        Macro: AC_CHECK_LIB
        Checks for the presence of certain C, C++, or Fortran library archive files.
        https://www.gnu.org/software/autoconf/manual/autoconf-2.67/html_node/Libraries.html#Libraries
        """
        lib_file, function_name = function.split(".")
        logger.info(f"AC_CHECK_LIB {lib_file}")
        package_name = AutotoolsClassifier._file_to_package(f"lib{lib_file}(.a|.so)$")
        return Dependency(package=package_name,
                          semantic_version=SimpleSpec("*"),
                          )

    @staticmethod
    def _pkg_check_modules(module_name, version=None):
        """
        Macro: PKG_CHECK_MODULES
        The main interface between autoconf and pkg-config.
        Provides a very basic and easy way to check for the presence of a
        given package in the system.
        """
        if not version:
            version="*"
        module_file = module_name
        logger.info(f"PKG_CHECK_MODULES {module_file}, {version}")
        package_name = AutotoolsClassifier._file_to_package(module_file)
        return Dependency(package=package_name,
                          semantic_version=SimpleSpec(version),
                          )

    @staticmethod
    @functools.lru_cache(maxsize=128)
    def _replace_variables(token:str, configure:str):
        """
            Search all variable occurrences in token and then try to find
            bindings for them in the configure script.
        """
        if "$" not in token:
            return token
        vars = re.findall(r"\$([a-zA-Z_0-9]+)|\${([_a-zA-Z0-9]+)}", token)
        vars = set(var for var in itertools.chain(*vars) if var)  #remove dups and empty
        for var in vars:
            logger.info(f"Trying to find bindings for {var} in configure")

            # This tries to find a single assign to the variable in question
            # ... var= "SOMETHING"
            # We ignore the fact thst variables could also appear in other constructs
            # For example:
            #    for var in THIS THAT ;
            # TODO/CHALLENGE Merge this two \/
            solutions = re.findall(f"{var}=\\s*\"([^\"]*)\"", configure)
            solutions += re.findall(f"{var}=\\s*'([^']*)'", configure)
            if len(solutions) > 1:
                logger.warning(f"Found several solutions for {var}: {solutions}")
            if len(solutions) == 0:
                logger.warning(f"No solution found for binding {var}")
                continue
            logger.info(f"Found a solution {solutions}")
            sol = (solutions+[None,])[0]
            if sol is not None:
                token = token.replace(f"${var}", sol).replace(f"${{{var}}}", sol)
        if "$" in token:
            raise Exception(f"Could not find a binding for variable/s in {token}")
        return token

    def _get_dependencies(self, path):
        #assert self.is_available()
        #assert self.can_classify(path)
        logger.info(f"Getting dependencies for autotool repo {os.path.abspath(path)}")
        orig_dir = getcwd()
        chdir(path)
        try:
            trace = subprocess.check_output(
                ["autoconf", "-t", 'AC_CHECK_HEADER:$n:$1',
                             "-t", 'AC_CHECK_LIB:$n:$1.$2',
                             "-t", 'PKG_CHECK_MODULES:$n:$2',
                             "-t", 'PKG_CHECK_MODULES_STATIC:$n', "./configure.ac"]).decode("utf8")
            configure = subprocess.check_output(["autoconf","./configure.ac"]).decode("utf8")
        finally:
            chdir(orig_dir)



        deps=[]
        for macro in trace.split('\n'):
            logger.debug(f"Handling: {macro}")
            macro, *arguments = macro.split(":")
            try:
                arguments = tuple(self._replace_variables(arg, configure) for arg in arguments)
            except Exception as e:
                logger.info(str(e))
                continue
            try:
                if macro == "AC_CHECK_HEADER":
                    deps.append(self._ac_check_header(header_file=arguments[0]))
                elif macro == "AC_CHECK_LIB":
                    deps.append(self._ac_check_lib(function=arguments[0]))
                elif macro == "PKG_CHECK_MODULES":
                    module_name, *version = arguments[0].split(" ")
                    deps.append(self._pkg_check_modules(module_name=module_name, version="".join(version)))
                else:
                    logger.error("Macro not supported", macro)
            except Exception as e:
                logger.error(str(e))
                continue

        '''
        # Identity of this package.
        PACKAGE_NAME='Bitcoin Core'
        PACKAGE_TARNAME='bitcoin'
        PACKAGE_VERSION='21.99.0'
        PACKAGE_STRING='Bitcoin Core 21.99.0'
        PACKAGE_BUGREPORT='https://github.com/bitcoin/bitcoin/issues'
        PACKAGE_URL='https://bitcoincore.org/'''
        package_name = self._replace_variables("$PACKAGE_NAME", configure)
        package_version = self._replace_variables("$PACKAGE_VERSION", configure)

        yield Package(
            name=package_name,
            version=Version.coerce(package_version),
            source="autotools",
            dependencies=deps
        )

    def classify(
            self,
            path: str,
            resolvers: Iterable[DependencyResolver] = (),
            cache: Optional[PackageCache] = None
    ) -> DependencyResolver:
        return DependencyResolver(self._get_dependencies(path), source=self, cache=cache)
