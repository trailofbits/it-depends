import re
import tempfile
import os
from os import chdir, getcwd
import shutil
import subprocess
from typing import Dict, Iterable, Iterator, List, Optional, Tuple, Union
from it_depends.ubuntu.apt import (
    cached_file_to_package as file_to_package,
    search_package,
)
import logging

try:
    # Used to parse the cmake trace. If this is not installed the plugin will
    # report itself as unavailable later
    import parse_cmake.parsing as cmake_parsing
except ImportError:
    cmake_parsing = None

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
from .ubuntu.docker import run_command

logger = logging.getLogger(__name__)


class CMakeResolver(DependencyResolver):
    """This attempts to parse CMakelists.txt in an cmake based repo.

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

    def is_available(self) -> ResolverAvailability:
        if cmake_parsing is None:
            return ResolverAvailability(
                False,
                "`parse_cmake` does not appear to be installed! "
                "Please run `pip install parse_cmake`",
            )

        if shutil.which("cmake") is None:
            return ResolverAvailability(
                False,
                "`cmake` does not appear to be installed! "
                "Make sure it is installed and in the PATH.",
            )

        return ResolverAvailability(True)

    def can_resolve_from_source(self, repo: SourceRepository) -> bool:
        return self.is_available and (repo.path / "CMakeLists.txt").exists()

    def _find_package(
        self,
        package: str,
        *args,
        file_to_package_cache: Optional[List[Tuple[str, str]]] = None,
    ) -> Iterator[Tuple[str, Optional[str]]]:
        """
        The command searches for a file called <PackageName>Config.cmake or <lower-case-package-name>-config.cmake for
        each name specified.

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
        keywords = (
            "EXACT",
            "QUIET",
            "REQUIRED",
            "COMPONENTS",
            "components...",
            "OPTIONAL_COMPONENTS",
            "CONFIG",
            "NO_MODULE",
            "NO_POLICY_SCOPE",
            "NAMES",
            "CONFIGS",
            "HINTS",
            "PATHS",
            "PATH_SUFFIXES",
            "NO_DEFAULT_PATH",
            "NO_PACKAGE_ROOT_PATH",
            "NO_CMAKE_PATH",
            "NO_CMAKE_ENVIRONMENT_PATH",
            "NO_SYSTEM_ENVIRONMENT_PATH",
            "NO_CMAKE_PACKAGE_REGISTRY",
            "NO_CMAKE_BUILDS_PATH",
            "NO_CMAKE_SYSTEM_PATH",
            "NO_CMAKE_SYSTEM_PACKAGE_REGISTRY",
            "CMAKE_FIND_ROOT_PATH_BOTH",
            "ONLY_CMAKE_FIND_ROOT_PATH",
            "NO_CMAKE_FIND_ROOT_PATH",
        )
        version = None
        if len(args) > 0 and args[0] not in keywords:
            version = args[0]
        name = re.escape(package)
        try:
            name = rf"({name}\.pc|{name}Config\.cmake|{name}Config\.cmake|{name.lower()}\-config\.cmake)"
            yield file_to_package(name, file_to_package_cache=file_to_package_cache), version
        except ValueError:
            found_package = search_package(package)

            contents = run_command("apt-file", "list", found_package).decode("utf8")
            if file_to_package_cache is not None:
                for line in contents.split("\n"):
                    if ": " not in line:
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
            if keyword.upper() in (
                "REQUIRED",
                "QUIET",
                "NO_CMAKE_PATH",
                "NO_CMAKE_ENVIRONMENT_PATH",
                "IMPORTED_TARGET",
                "GLOBAL",
            ):
                continue
            module_name = re.split("(<|>|=|<=|>=)", keyword)[0]
            version_range = keyword[len(module_name) :].strip()
            if not version_range:
                version_range = None
            module_specs.append((module_name, version_range))

        for module_name, version_range in module_specs:
            yield (
                file_to_package(
                    rf"{re.escape(module_name)}\.pc",
                    file_to_package_cache=file_to_package_cache,
                ),
                version_range,
            )

    def _get_names(self, args, keywords):
        """Get the sequence of argumens after NAMES and until any of the keywords"""

        index = -1
        if "NAMES" in args:
            index = args.index("NAMES")
        names = []
        if index != -1:
            for name in args[index + 1 :]:
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
        keywords = (
            "NAMES_PER_DIR",
            "HINTS",
            "PATHS",
            "PATH_SUFFIXES" "DOC",
            "REQUIRED",
            "NO_DEFAULT_PATH",
            "NO_PACKAGE_ROOT_PATH",
            "NO_CMAKE_PATH",
            "NO_CMAKE_ENVIRONMENT_PATH",
            "NO_SYSTEM_ENVIRONMENT_PATH",
            "NO_CMAKE_SYSTEM_PATH",
            "CMAKE_FIND_ROOT_PATH_BOTH",
            "ONLY_CMAKE_FIND_ROOT_PATH",
            "NO_CMAKE_FIND_ROOT_PATH",
        )
        args = self._get_names(args[1:], keywords)

        names = set()
        for name in args:
            if not name.startswith("lib"):
                name = f"lib{name}"
            names.add(name)
        yield file_to_package(
            rf"({ '|'.join(map(re.escape, names))})(\.so[0-9\.]*|\.a)",
            file_to_package_cache=file_to_package_cache,
        ), None

    def _check_include_files(self, includes, variable, *args, file_to_package_cache=None):
        """CHECK_INCLUDE_FILES("<includes>" <variable> [LANGUAGE <language>])
        https://cmake.org/cmake/help/latest/module/CheckIncludeFiles.html
        """
        pattern = r"include/(.*/)*(" + "|".join(map(re.escape, args[0].split(";"))) + r")"
        yield file_to_package(pattern, file_to_package_cache=file_to_package_cache), None

    def _check_include_file(self, include_file, *args, file_to_package_cache=None):
        """
        CHECK_INCLUDE_FILE(<include> <variable> [<flags>])
        https://cmake.org/cmake/help/latest/module/CheckIncludeFile.html#module:CheckIncludeFile
        """
        pattern = rf"include/(.*/)*{re.escape(include_file)}"
        yield file_to_package(pattern, file_to_package_cache=file_to_package_cache), None

    def _check_include_file_cxx(self, include_file, *args, file_to_package_cache=None):
        """
        CHECK_INCLUDE_FILE_CXX(INCLUDE VARIABLE)
        https://cmake.org/cmake/help/v3.0/module/CheckIncludeFileCXX.html
        """
        yield from self._check_include_file(
            include_file, *args, file_to_package_cache=file_to_package_cache
        )

    def _find_path(self, var, *args, file_to_package_cache=None):
        """find_path (<VAR> name1 [path1 path2 ...])
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
        keywords = (
            "HINTS",
            "PATHS",
            "PATH_SUFFIXES,",
            "DOC",
            "REQUIRED",
            "NO_DEFAULT_PATH",
            "NO_PACKAGE_ROOT_PATH",
            "NO_CMAKE_PATH",
            "NO_CMAKE_ENVIRONMENT_PATH",
            "NO_SYSTEM_ENVIRONMENT_PATH",
            "NO_CMAKE_SYSTEM_PATH",
            "CMAKE_FIND_ROOT_PATH_BOTH",
            "ONLY_CMAKE_FIND_ROOT_PATH",
            "NO_CMAKE_FIND_ROOT_PATH",
        )
        for name in args:
            if name == "NAMES":
                continue
            if name in keywords:
                break
            try:
                yield file_to_package(
                    f"{re.escape(name)}", file_to_package_cache=file_to_package_cache
                ), None
                break
            except Exception as e:
                logger.debug(e)

    def resolve_from_source(
        self, repo: SourceRepository, cache: Optional[PackageCache] = None
    ) -> Optional[SourcePackage]:
        if not self.can_resolve_from_source(repo):
            return None

        path = repo.path
        logger.info(f"Getting dependencies for cmake repo {path}")
        apath = str(path.absolute())
        orig_dir = getcwd()
        try:
            with tempfile.TemporaryDirectory() as tmpdirname:
                logger.debug(f"Created temporary directory {tmpdirname}")
                # This is a hack to modify the original cmake language temporarily
                # TODO: Think a better way that does not modify the original repo
                # Maybe use newer cmake feature:
                # https://cmake.org/cmake/help/latest/manual/cmake-file-api.7.html#manual:cmake-file-api(7)
                orig_cmakelists = os.path.join(apath, "CMakeLists.txt")
                backup = os.path.join(tmpdirname, "backup")
                shutil.copyfile(orig_cmakelists, backup)
                output = os.path.join(tmpdirname, "output")
                build_dir = os.path.join(tmpdirname, "build")
                os.mkdir(build_dir)
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
                    p = subprocess.run(
                        [
                            "cmake",
                            "-Wno-dev",
                            "--trace",
                            "--trace-expand",
                            f"--trace-redirect={output}",
                            apath,
                        ],
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        cwd=build_dir,
                    )
                    if p.returncode != 0:
                        logger.error(
                            f"Error running cmake:\n{p.stdout.decode('utf-8')}\n{p.stderr.decode('utf-8')}"
                        )
                        exit(1)
                    with open(output, "rt") as outfd:
                        trace = outfd.read()
                finally:
                    shutil.copyfile(backup, orig_cmakelists)
        finally:
            chdir(orig_dir)

        package_name = None
        package_version = None

        file_to_package_cache: List[Tuple[str, str]] = []
        deps: List[Tuple[str, Optional[str]]] = []
        bindings = {}
        try:
            for line in re.split(r"/.*\([0-9]+\):\s+", trace):
                if not line:
                    continue

                line = line.strip("\n")
                parsed: Iterable[Union[cmake_parsing.BlankLine, cmake_parsing._Command]] = ()

                try:
                    parsed = cmake_parsing.parse(line)
                except Exception as e:
                    logger.debug(f"Parsing error: {e}")
                    pass  # ignore parsing exceptions for now

                for token in parsed:
                    try:
                        if isinstance(line, cmake_parsing.BlankLine):
                            continue
                        if isinstance(token, cmake_parsing._Command):
                            command = token.name.lower()
                            if command not in (
                                "project",
                                "set",
                                "find_library",
                                "find_path",
                                "check_include_file",
                                "check_include_file_cxx",
                                "check_include_files",
                                "find_package",
                                "pkg_check_modules",
                                "_pkg_find_libs",
                            ):
                                # It's a trace of cmake.
                                continue
                            body: List[str] = sum(
                                map(lambda x: x.contents.split(";"), token.body), []
                            )

                            if command not in ("set", "project"):
                                logger.info(f"Processing CMAKE command {command} {body}")

                            package_iter: Iterator[Tuple[str, Optional[str]]] = iter(())

                            # Dispatch over token name ...
                            if command == "project":
                                package_name = body[0]
                            elif command == "set":
                                # detect project version...
                                # TODO: Revisit the following type error:
                                value: Optional[str] = (body + [None,])[  # type: ignore
                                    1
                                ]  # type: ignore
                                if (
                                    package_name is not None
                                    and body[0].lower() == f"{package_name}_version"
                                ):
                                    package_version = value
                                bindings[body[0].upper()] = value
                            elif command == "find_package":
                                package_iter = self._find_package(
                                    *body, file_to_package_cache=file_to_package_cache
                                )
                            elif command == "find_path":
                                package_iter = self._find_path(
                                    *body, file_to_package_cache=file_to_package_cache
                                )
                            elif command == "find_library":
                                package_iter = self._find_library(
                                    *body, file_to_package_cache=file_to_package_cache
                                )
                            elif command == "pkg_check_modules":
                                package_iter = self._pkg_check_modules(
                                    *body, file_to_package_cache=file_to_package_cache
                                )
                            elif command == "check_include_file":
                                package_iter = self._check_include_file(
                                    *body, file_to_package_cache=file_to_package_cache
                                )
                            elif command in (
                                "check_include_files",
                                "check_include_file_cxx",
                            ):
                                package_iter = self._check_include_files(
                                    *body, file_to_package_cache=file_to_package_cache
                                )
                            else:
                                logger.warning(f"Not handled {token.name} {body}")
                            new_packages = tuple(package_iter)
                            deps.extend(new_packages)
                    except Exception as e:
                        logger.debug(e)
        except Exception as e:
            logger.debug(e)
            raise

        # remove "-dev"? and dupplicates
        depsd: Dict[str, Optional[str]] = {}
        for name, version in deps:
            if name not in depsd or depsd[name] is None:
                depsd[name] = version
            else:
                if version is not None and version != depsd[name]:
                    # conflict
                    logger.info(
                        f"Found a conflict in versions for {name} ({version} vs. {depsd[name]}). Setting '*'"
                    )
                    depsd[name] = "*"

        if package_version is None:
            package_version = "0.0.0"
        if package_name is None:
            package_name = path.name
            logger.warning(f"Unable to determine package name for {path}. Using {package_name}")

        return SourcePackage(
            name=package_name,
            version=Version.coerce(package_version),
            source=self.name,
            dependencies=(
                Dependency(
                    package=name,
                    semantic_version=SimpleSpec(version is None and "*" or version),
                    source="ubuntu",
                )
                for name, version in depsd.items()
            ),
            source_repo=repo,
        )
