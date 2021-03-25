import functools
import re
import tempfile
import os
from os import chdir, getcwd, path
from pathlib import Path
import shutil
import subprocess
from typing import Iterable
from .utils import cached_file_to_package as file_to_package, search_package
import logging
try:
    #Used to parse the cmake trace. If this is not installed the plugin will
    #report itself as unavailable later
    import parse_cmake.parsing as cmake_parsing
except:
    pass

logger = logging.getLogger(__name__)


from .dependencies import (
    ClassifierAvailability, Dependency, DependencyClassifier, DependencyResolver, Package, SimpleSpec, Version
)



class CMakeClassifier(DependencyClassifier):
    """ This attempts to parse CMakelists.txt in an cmake based repo.

    CMakelists.txt is patched so no errors ar fatal and then we trace cmake
    attempts to find the needed software.

    Every traced command is translated in a search to a specific file in the list
    of ubuntu packages. For example CHECK_INCLUDE_FILE(.. pthread ..) will look
    for a package that provides the file `.*/include/(.*/)*pthread.h$`

    With this approach the obtained packages list will depend on the current state
    of the host OS. Checks for packages/modules/libs are most of the time dependant
    on file existence but it could be decided by the means of executing arbitrary code.
    The checks cmake make are dependant on themselves and on the commandline
    arguments and on the environment arguments.

    CMakeClassifier obtains a superset of ubuntu packages that if installed may
    increase the possibility of a successful project compilation.


    IDEA: Find a minimal set of packages that provide all the needed files, instead
    of choosing one package for each file needed (and potentially adding 2 packages
    that provide same file)


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

    def _find_package(self, package, *args, file_to_package_cache=None):
        """
        The command searches for a file called <PackageName>Config.cmake or <lower-case-package-name>-config.cmake for each name specified.

        find_package(<PackageName> [version] [EXACT] [QUIET]
             [REQUIRED] [[COMPONENTS] [components...]]
             [OPTIONAL_COMPONENTS components...]
             [CONFIG|NO_MODULE]
             [NO_POLICY_SCOPE]
             [NAMES name1 [name2 ...]]
             [CONFIGS config1 [config2 ...]]
             [HINTS path1 [path2 ... ]]
             [PATHS path1 [path2 ... ]]
             [PATH_SUFFIXES suffix1 [suffix2 ...]]
             [NO_DEFAULT_PATH]
             [NO_PACKAGE_ROOT_PATH]
             [NO_CMAKE_PATH]
             [NO_CMAKE_ENVIRONMENT_PATH]
             [NO_SYSTEM_ENVIRONMENT_PATH]
             [NO_CMAKE_PACKAGE_REGISTRY]
             [NO_CMAKE_BUILDS_PATH] # Deprecated; does nothing.
             [NO_CMAKE_SYSTEM_PATH]
             [NO_CMAKE_SYSTEM_PACKAGE_REGISTRY]
             [CMAKE_FIND_ROOT_PATH_BOTH |
              ONLY_CMAKE_FIND_ROOT_PATH |
              NO_CMAKE_FIND_ROOT_PATH])
         Ref.https://cmake.org/cmake/help/latest/command/find_package.html
         """
        keywords = ("EXACT", "QUIET", "REQUIRED", "COMPONENTS", "components...", "OPTIONAL_COMPONENTS", "CONFIG", "NO_MODULE", "NO_POLICY_SCOPE", "NAMES", "CONFIGS", "HINTS", "PATHS", "PATH_SUFFIXES", "NO_DEFAULT_PATH", "NO_PACKAGE_ROOT_PATH", "NO_CMAKE_PATH", "NO_CMAKE_ENVIRONMENT_PATH", "NO_SYSTEM_ENVIRONMENT_PATH", "NO_CMAKE_PACKAGE_REGISTRY", "NO_CMAKE_BUILDS_PATH", "NO_CMAKE_SYSTEM_PATH", "NO_CMAKE_SYSTEM_PACKAGE_REGISTRY", "CMAKE_FIND_ROOT_PATH_BOTH", "ONLY_CMAKE_FIND_ROOT_PATH", "NO_CMAKE_FIND_ROOT_PATH",)
        version = None
        if len(args) > 0 and args[0] not in keywords:
            version = args[0]
        name = re.escape(package)
        try:
            name = f"({name}\.pc|{name}Config\.cmake|{name.lower()}Config\.cmake|{name.lower()}\-config\.cmake)"
            yield file_to_package(name, file_to_package_cache=file_to_package_cache), version
        except:
            found_package = search_package(package)

            contents = subprocess.run(["apt-file", "list", found_package],
                                      stdout=subprocess.PIPE).stdout.decode("utf8")
            for line in contents.split("\n"):
                if not ": " in line:
                    continue
                package_i, filename_i = line.split(": ")
                file_to_package_cache.append((package_i, filename_i))

            yield found_package, version

    def _pkg_check_modules(self, prefix, *args, file_to_package_cache=None):
        """
        pkg_check_modules(<prefix>
                  [REQUIRED] [QUIET]
                  [NO_CMAKE_PATH]
                  [NO_CMAKE_ENVIRONMENT_PATH]
                  [IMPORTED_TARGET [GLOBAL]]
                  <moduleSpec> [<moduleSpec>...])
          checks for given modules and uses the first working one

        https://cmake.org/cmake/help/latest/module/FindPkgConfig.html
        """
        module_specs = []
        for keyword in args:
            if keyword.upper() in ("REQUIRED", "QUIET", "NO_CMAKE_PATH", "NO_CMAKE_ENVIRONMENT_PATH", "IMPORTED_TARGET", "GLOBAL"):
                continue
            module_name = re.split("(<|>|=|<=|>=)", keyword)[0]
            version_range = keyword[len(module_name):].strip()
            if not version_range:
                version_range=None
            module_specs.append((module_name, version_range))

        for module_name, version_range in module_specs:
            yield file_to_package(f"{re.escape(module_name)}\.pc", file_to_package_cache=file_to_package_cache), version_range

    def _get_names(self, args, keywords):
        """ Get the sequence of argumens after NAMES and until any of the keywords"""

        index = -1
        if "NAMES" in args:
            index = args.index("NAMES")
        names = []
        if index != -1:
            for name in args[index+1:]:
                if any(map(name.startswith, keywords)):
                    break
                names.extend(name.split(";"))
        return names

    def _find_library(self, *args, file_to_package_cache=None):
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

        names = set()
        for name in args:
            if not name.startswith("lib"):
                name = f"lib{name}"
            names.add(name)
        yield file_to_package(f"({ '|'.join(map(re.escape, names))})(\.so[0-9\.]*|\.a)", file_to_package_cache=file_to_package_cache), None

    def _check_include_files(self, includes, variable, *args, file_to_package_cache=None):
        """CHECK_INCLUDE_FILES("<includes>" <variable> [LANGUAGE <language>])
        https://cmake.org/cmake/help/latest/module/CheckIncludeFiles.html
        """
        pattern = "include/(.*/)*(" + "|".join(map(re.escape, args[0].split(";"))) + ")"
        yield file_to_package(pattern, file_to_package_cache=file_to_package_cache), None


    def _check_include_file(self, include_file, *args, file_to_package_cache=None):
        """
        CHECK_INCLUDE_FILE(<include> <variable> [<flags>])
        https://cmake.org/cmake/help/latest/module/CheckIncludeFile.html#module:CheckIncludeFile
        """
        pattern = f"include/(.*/)*{re.escape(include_file)}"
        yield file_to_package(pattern, file_to_package_cache=file_to_package_cache), None

    def _check_include_file_cxx(self, include_file, *args, file_to_package_cache=None):
        """
        CHECK_INCLUDE_FILE_CXX(INCLUDE VARIABLE)
        https://cmake.org/cmake/help/v3.0/module/CheckIncludeFileCXX.html
        """
        yield from self._check_include_file(include_file, *args, file_to_package_cache=file_to_package_cache)


    def _find_path(self, var, *args, file_to_package_cache=None):
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
        keywords = ("HINTS", "PATHS", "PATH_SUFFIXES,", "DOC", "REQUIRED",
                    "NO_DEFAULT_PATH", "NO_PACKAGE_ROOT_PATH", "NO_CMAKE_PATH",
                    "NO_CMAKE_ENVIRONMENT_PATH", "NO_SYSTEM_ENVIRONMENT_PATH",
                    "NO_CMAKE_SYSTEM_PATH", "CMAKE_FIND_ROOT_PATH_BOTH",
                    "ONLY_CMAKE_FIND_ROOT_PATH", "NO_CMAKE_FIND_ROOT_PATH")
        #args = self._get_names(args, keywords)
        for name in args:
            if name == "NAMES":
                continue
            if name in keywords:
                break
            try:
                yield file_to_package(f"{re.escape(name)}", file_to_package_cache=file_to_package_cache), None
                break
            except Exception as e:
                logger.debug(e)

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
                # Maybe use newer cmake feature:
                # https://cmake.org/cmake/help/latest/manual/cmake-file-api.7.html#manual:cmake-file-api(7)
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
                    subprocess.run(
                         ["cmake", "-Wno-dev", "-trace", "--trace-expand", f"--trace-redirect={output}", apath], stdout=subprocess.PIPE).stdout.decode("utf8")
                    with open(output, "rt") as outfd:
                        trace = outfd.read()
                finally:
                    shutil.copyfile(backup, orig_cmakelists)
        finally:
            chdir(orig_dir)

        package_name = None
        package_version = None

        file_to_package_cache = []
        deps = []
        bindings = {}
        try:
            for line in (re.split("/.*\([0-9]+\):  ", trace)):
                if not line:
                    continue

                line = line.strip("\n")
                parsed = ()

                try:
                    parsed = cmake_parsing.parse(line)
                except Exception as e:
                    logger.debug("Parsing error", e)
                    pass #ignore parsing exceptions for now

                for token in parsed:
                    try:
                        if isinstance(line, cmake_parsing.BlankLine):
                            continue
                        if isinstance(token, cmake_parsing._Command):
                            command = token.name.lower()
                            if command not in ('project', 'set', 'find_library', 'find_path', 'check_include_file','check_include_file_cxx', 'check_include_files', 'find_package', 'pkg_check_modules', '_pkg_find_libs'):
                                #It's a trace of cmake.
                                continue
                            body = sum(map(lambda x: x.contents.split(";"), token.body), [])

                            new_packages = ()
                            if command not in ("set", "project"):
                                logger.info(f"Processing CMAKE command {command} {body}")

                            #Dispatch over token name ...
                            if command == "project":
                                package_name = body[0]
                            elif command == "set":
                                #detect project version...
                                value = (body + [None,])[1]
                                if package_name is not None and body[0].lower() == f"{package_name}_version":
                                    package_version = value
                                bindings[body[0].upper()] = value
                            elif command == "find_package":
                                new_packages = self._find_package(*body, file_to_package_cache=file_to_package_cache)
                            elif command == "find_path":
                                new_packages = self._find_path(*body, file_to_package_cache=file_to_package_cache)
                            elif command == "find_library":
                                new_packages = self._find_library(*body, file_to_package_cache=file_to_package_cache)
                            elif command == "pkg_check_modules":
                                new_packages = self._pkg_check_modules(*body, file_to_package_cache=file_to_package_cache)
                            elif command == "check_include_file":
                                new_packages = self._check_include_file(*body, file_to_package_cache=file_to_package_cache)
                            elif command in ("check_include_files", "check_include_file_cxx"):
                                new_packages = self._check_include_files(*body, file_to_package_cache=file_to_package_cache)
                            else:
                                logger.info(f"Not handled {token.name} {body}")
                            new_packages = tuple(new_packages)
                            deps.extend(new_packages)
                            if command not in ("set", "project"):
                                logger.info(f"    GOT: {new_packages}")


                    except Exception as e:
                        logger.debug(e)
        except Exception as e:
            logger.debug(e)
            raise

        #remove "-dev" and dupplicates
        depsd = {}
        for name, version in deps:
            name = name.replace("-dev", "")
            if name not in depsd or depsd[name] == None:
                depsd[name] = version
            else:
                if version != None and version != depsd[name]:
                    # conflict
                    logger.info(f"Found a conflict in versions for {name} ({version} vs. {depsd[name]}). Setting '*'")
                    depsd[name] = "*"

        if package_version is None:
            package_version = "0.0.0"
        yield Package(
            name=package_name,
            version=Version.coerce(package_version),
            source="cmake",
            dependencies=(Dependency(package=name, semantic_version=SimpleSpec(version is None and "*" or version)) for name,version in depsd.items())
        )

    def classify(self, path: str, resolvers: Iterable[DependencyResolver] = ()) -> DependencyResolver:
        return DependencyResolver(self._get_dependencies(path), source=self)
