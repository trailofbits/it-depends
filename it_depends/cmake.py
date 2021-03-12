import functools
import re
import tempfile
import os
from os import chdir, getcwd, path
from pathlib import Path
import shutil
import subprocess
from typing import Iterable
from .utils import file_to_package, get_apt_packages
import logging
try:
    import parse_cmake.parsing as cmake_parsing
except:
    pass

logger = logging.getLogger(__name__)


from .dependencies import (
    ClassifierAvailability, Dependency, DependencyClassifier, DependencyResolver, Package, SimpleSpec, Version
)


class CMakeClassifier(DependencyClassifier):
    """ This attempts to parse CMakelists.txt in an cmake based repo.
    """
    name = "cmake"
    description = "classifies the dependencies of native/cmake packages parsing CMakeLists.txt"

    def is_available(self) -> ClassifierAvailability:
        try:
            import parse_cmake
        except ImportError as e:
            return ClassifierAvailability(False, "`parse_cmake` does not appear to be installed! "
                                                 "Please run `pip install parse_cmake`")

        if shutil.which("cmake") is None:
            return ClassifierAvailability(False, "`cmake` does not appear to be installed! "
                                                 "Make sure it is installed and in the PATH.")

        return ClassifierAvailability(True)

    def can_classify(self, path: str) -> bool:
        return (Path(path) / "CMakeLists.txt").exists()

    @staticmethod
    @functools.lru_cache(maxsize=128)
    def _find_package(*args):
        print ("FIND_PACKAGE", args)
        name = re.escape(args[0])
        try:
            name = f"({name}\.pc|{name}Config\.cmake|{name.lower()}Config\.cmake)"
            return file_to_package(name)
        except:
            for apt_package in get_apt_packages():
                if args[0].lower() not in apt_package:
                    continue
                if re.match(f"(lib)*{re.escape(args[0]).lower()}(\-*([0-9]*)(\.*))*(\-dev)*", apt_package):
                    return apt_package
        raise Exception("RR")


    def _pkg_check_modules(self, *args):
        """
        pkg_check_modules(<PREFIX> [REQUIRED] [QUIET] <MODULE> [<MODULE>]*)
          checks for all the given modules
        pkg_search_module(<PREFIX> [REQUIRED] [QUIET] <MODULE> [<MODULE>]*)
          checks for given modules and uses the first working one

        https://cmake.org/cmake/help/latest/module/FindPkgConfig.html
        """
        args = args[1:]
        while args[0].upper() in ("REQUIRED", "QUITE"):
            args = args[1:]
        return file_to_package(re.escape("(" + "|".join(map(re.escape, args))+")" ))

    def _get_names(self, args, keywords):
        """ Get the sequence of argumens after NAMES until any of the keywords"""

        index = args.index("NAMES")
        names = []
        if index != -1:
            for name in args[index+1:]:
                if any(map(name.startswith, keywords)):
                    break
                names.extend(name.split(";"))
        return names

    def _find_library(self, *args):
        """find_library (
          <VAR>
          name | NAMES name1 [name2 ...] [NAMES_PER_DIR]
          [HINTS path1 [path2 ... ENV var]]
          [PATHS path1 [path2 ... ENV var]]
          [PATH_SUFFIXES suffix1 [suffix2 ...]]
          [DOC "cache documentation string"]
          [REQUIRED]
          [NO_DEFAULT_PATH]
          [NO_PACKAGE_ROOT_PATH]
          [NO_CMAKE_PATH]
          [NO_CMAKE_ENVIRONMENT_PATH]
          [NO_SYSTEM_ENVIRONMENT_PATH]
          [NO_CMAKE_SYSTEM_PATH]
          [CMAKE_FIND_ROOT_PATH_BOTH |
           ONLY_CMAKE_FIND_ROOT_PATH |
           NO_CMAKE_FIND_ROOT_PATH]
         )

        https://cmake.org/cmake/help/latest/command/find_library.html
        """
        keywords = ("NAMES_PER_DIR", "HINTS", "PATHS", "PATH_SUFFIXES" "DOC", "REQUIRED",
         "NO_DEFAULT_PATH", "NO_PACKAGE_ROOT_PATH", "NO_CMAKE_PATH",
         "NO_CMAKE_ENVIRONMENT_PATH", "NO_SYSTEM_ENVIRONMENT_PATH",
         "NO_CMAKE_SYSTEM_PATH", "CMAKE_FIND_ROOT_PATH_BOTH",
         "ONLY_CMAKE_FIND_ROOT_PATH", "NO_CMAKE_FIND_ROOT_PATH")
        args = self._get_names(args[1:], keywords)
        print ("FIND_LIBRARY", args)

        names = set()
        for name in args:
            if not name.startswith("lib"):
                name = f"lib{name}"
            names.add(name)
        return file_to_package(f"({ '|'.join(map(re.escape, names))})(\.so|\.so\.[0-9]|\.a)")

    def check_include_files(self, *args):
        """CHECK_INCLUDE_FILES("<includes>" <variable> [LANGUAGE <language>])
        https://cmake.org/cmake/help/latest/module/CheckIncludeFiles.html
        """
        print ("check_inlude_files", args)

        pattern = "/usr/include/(.*/    )*(" + "|".join(map(re.escape, args[0].split(";"))) + ")$"
        return file_to_package(pattern)

    def _find_path(self, *args):
        """ find_path (<VAR> name1 [path1 path2 ...])
        find_path (
          <VAR>
          name | NAMES name1 [name2 ...]
          [HINTS path1 [path2 ... ENV var]]
          [PATHS path1 [path2 ... ENV var]]
          [PATH_SUFFIXES suffix1 [suffix2 ...]]
          [DOC "cache documentation string"]
          [REQUIRED]
          [NO_DEFAULT_PATH]
          [NO_PACKAGE_ROOT_PATH]
          [NO_CMAKE_PATH]
          [NO_CMAKE_ENVIRONMENT_PATH]
          [NO_SYSTEM_ENVIRONMENT_PATH]
          [NO_CMAKE_SYSTEM_PATH]
          [CMAKE_FIND_ROOT_PATH_BOTH |
           ONLY_CMAKE_FIND_ROOT_PATH |
           NO_CMAKE_FIND_ROOT_PATH]
         )
        https://cmake.org/cmake/help/latest/command/find_path.html#command:find_path
        """
        print("FIND_PATH:", args)
        keywords = ("HINTS", "PATHS", "PATH_SUFFIXES,", "DOC", "REQUIRED",
                    "NO_DEFAULT_PATH", "NO_PACKAGE_ROOT_PATH", "NO_CMAKE_PATH",
                    "NO_CMAKE_ENVIRONMENT_PATH", "NO_SYSTEM_ENVIRONMENT_PATH",
                    "NO_CMAKE_SYSTEM_PATH", "CMAKE_FIND_ROOT_PATH_BOTH",
                    "ONLY_CMAKE_FIND_ROOT_PATH", "NO_CMAKE_FIND_ROOT_PATH")
        args = self._get_names(args[1:], keywords)
        for name in args:
            try:
                return file_to_package(f"{re.escape(name)}")
            except Exception as e:
                logger.debug(e)
                pass
        raise Exception("SS")

    def _get_dependencies(self, path):
        #assert self.is_available()
        #assert self.can_classify(path)
        apath = os.path.abspath(path)
        logger.info(f"Getting dependencies for cmake repo {apath}")
        orig_dir = getcwd()
        try:
            with tempfile.TemporaryDirectory() as tmpdirname:
                logger.info(f'Created temporary directory {tmpdirname}')
                # This is a hack to modify the original cmake language temporarily
                # TODO: Think a better way that does not modify the original repo
                orig_cmakelists = os.path.join(apath, "CMakeLists.txt")
                backup = os.path.join(tmpdirname, "backup")
                shutil.copyfile(orig_cmakelists, backup)
                output = os.path.join(tmpdirname, 'output')
                try:
                    # Replaces the message function by a no-op
                    # Not that message(FATAL_ERROR ...) terminates cmake
                    with open(orig_cmakelists, "r+t") as cmake_lists:
                        patched = """function(message)\nendfunction()\n"""
                        patched += cmake_lists.read()
                        cmake_lists.seek(0)
                        cmake_lists.write(patched)
                        cmake_lists.flush()
                    cmake_lists.close()
                    subprocess.check_output(
                         ["cmake", "-Wno-dev", "-trace", "--trace-expand", f"--trace-redirect={output}", apath]).decode("utf8")
                    with open(output, "rt") as outfd:
                        trace = outfd.read()
                finally:
                    shutil.copyfile(backup, orig_cmakelists)
        finally:
            chdir(orig_dir)

        package_name = None
        package_version = None

        deps = []
        bindings = {}
        package_name = None
        try:
            for line in (re.split("/.*\([0-9]+\):  ", trace)):
                if not line:
                    continue

                line = line.strip("\n")
                parsed = ()

                try:
                    parsed = cmake_parsing.parse(line)
                except Exception as e:
                    #logger.debug("Parsing error", e)
                    pass #ignore parsing exceptions for now

                for token in parsed:
                    try:
                        if isinstance(line, cmake_parsing.BlankLine):
                            continue
                        if isinstance(token, cmake_parsing._Command):
                            if token.name.lower() not in set(('set', 'find_library', 'find_path', 'check_include_files', 'find_package', 'pkg_check_modules', 'check_symbol_exists', 'check_include_file', 'project', 'check_for_dir', 'check_function_exists', '_pkg_find_libs', 'check_include_file_cxx')):
                                #It's a trace of cmake.
                                continue
                            body = tuple(map(lambda x:x.contents, token.body))

                            #Dispatch over token name ...
                            if token.name.lower() == "find_package":
                                deps.append(self._find_package(*body))
                            elif token.name.lower() == "find_path":
                                deps.append(self._find_path(*body))
                            elif token.name.lower() == "find_library":
                                deps.append(self._find_library(*body))
                            elif token.name.lower() == "project":
                                package_name = body[0]
                            elif token.name.lower() == "set":
                                #detect project version...
                                if package_name is not None and body[0].lower() == f"{package_name}_version":
                                    package_version = body[1]
                            elif token.name.lower() == "pkg_check_modules":
                                deps.append(self._pkg_check_modules(body))
                            else:
                                pass
                                print(token)
                            """
                            elif token.name == "execute_process":
                                print("EXECUTE", line)
                            elif token.name.lower() == "pkg_check_modules":
                                self._pkg_check_modules(*body)
                            elif token.name.lower() == "check_include_files":
                                self._check_include_files(*body)
                                
                                
Depends: libc6 (>= 2.29), libfontconfig1 (>= 2.12.6), libfreetype6 (>= 2.2.1), libjpeg8 (>= 8c), liblcms2-2 (>= 2.2+git20110628), libnspr4 (>= 2:4.9-2~), libnss3 (>= 2:3.16), libopenjp2-7 (>= 2.0.0), libpng16-16 (>= 1.6.2-1), libstdc++6 (>= 5.2), libtiff5 (>= 4.0.3), zlib1g (>= 1:1.1.4)
Recommends: poppler-data

                            """
                    except Exception as e:
                        pass
        except Exception as e:
            pass


        yield Package(
            name=package_name,
            version=Version.coerce(package_version),
            source="cmake",
            dependencies=map(lambda name : Dependency(package=name, semantic_version=SimpleSpec("*")), deps)
        )

    def classify(self, path: str, resolvers: Iterable[DependencyResolver] = ()) -> DependencyResolver:
        return DependencyResolver(self._get_dependencies(path), source=self)
