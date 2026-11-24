"""CMake dependency resolution."""

from __future__ import annotations

import re
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from it_depends._commands import run_command as execute
from it_depends.reporting import Severity, diagnostic

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator

    from it_depends.models import Package

from it_depends.ubuntu.apt import (
    cached_file_to_package as file_to_package,
)
from it_depends.ubuntu.apt import (
    make_cmake_config_query,
    make_cmake_include_query,
    make_library_query,
    make_path_query,
    make_pkg_config_query,
    search_package,
)

try:
    # Used to parse the cmake trace. If this is not installed the plugin will
    # report itself as unavailable later
    import parse_cmake.parsing as cmake_parsing
except ImportError:
    cmake_parsing = None

from it_depends._exec import resolve_executable
from it_depends.dependencies import (
    Dependency,
    DependencyResolver,
    PackageCache,
    ResolverAvailability,
    SimpleSpec,
    SourcePackage,
    SourceRepository,
    Version,
)
from it_depends.ubuntu.docker import run_command


def _configure_trace(cmake: str, project: Path) -> str:
    """Trace a private copy of the top-level script with project-relative files available."""
    with tempfile.TemporaryDirectory() as directory:
        scratch = Path(directory)
        project = project.absolute()
        source = scratch / "source" / project.name
        source.mkdir(parents=True)
        for entry in project.iterdir():
            if entry.name == "CMakeLists.txt":
                script = entry.read_bytes().removeprefix(b"\xef\xbb\xbf")
                (source / entry.name).write_bytes(b"function(message)\nendfunction()\n" + script)
            elif entry != scratch:
                (source / entry.name).symlink_to(entry, target_is_directory=entry.is_dir())
        output = scratch / "trace"
        result = execute(
            [
                cmake,
                "-Wno-dev",
                "--trace-expand",
                f"--trace-redirect={output}",
                "-S",
                str(source),
                "-B",
                str(scratch / "build"),
            ],
            check=False,
            capture_output=True,
        )
        if result.returncode:
            diagnostic(
                Severity.ERROR,
                __name__,
                "Error running cmake:\n%s\n%s",
                result.stdout.decode("utf-8", errors="replace"),
                result.stderr.decode("utf-8", errors="replace"),
            )
        return output.read_text()


class CMakeResolver(DependencyResolver):
    """Parse CMakeLists.txt in a cmake-based repository.

    A temporary copy of CMakeLists.txt suppresses message() calls while CMake traces
    attempts to find the needed software.

    Every traced command is translated in a search to a specific file in the list
    of ubuntu packages. For example CHECK_INCLUDE_FILE(.. pthread ..) will look
    for a package that provides the file `.*/include/(.*/)*pthread.h$`

    With this approach the obtained packages list will depend on the current state
    of the host OS. Checks for packages/modules/libs are most of the time dependent
    on file existence but it could be decided by the means of executing arbitrary code.
    The checks cmake make are dependent on themselves and on the commandline
    arguments and on the environment arguments.

    CMakeClassifier obtains a superset of ubuntu packages that if installed may
    increase the possibility of a successful project compilation.

    IDEA: Find a minimal set of packages that provide all the needed files, instead
    of choosing one package for each file needed (and potentially adding 2 packages
    that provide same file)
    """

    name = "cmake"
    description = "classifies the dependencies of native/cmake packages parsing CMakeLists.txt"
    _tool_path: str | None = None

    @property
    def tool_path(self) -> str:
        """Resolve and cache the path to the cmake executable."""
        if self._tool_path is None:
            self._tool_path = resolve_executable("cmake")
        return self._tool_path

    def is_available(self) -> ResolverAvailability:
        """Check if CMake resolver is available."""
        if cmake_parsing is None:
            return ResolverAvailability(
                is_available=False,
                reason="`parse_cmake` does not appear to be installed! Please run `pip install parse_cmake`",
            )

        try:
            _ = self.tool_path
        except FileNotFoundError:
            return ResolverAvailability(
                is_available=False,
                reason="`cmake` does not appear to be installed! Make sure it is installed and in the PATH.",
            )

        return ResolverAvailability(is_available=True)

    def can_resolve_from_source(self, repo: SourceRepository) -> bool:
        """Check if CMake resolver can resolve from source repository."""
        return bool(self.is_available()) and (repo.path / "CMakeLists.txt").exists()

    def _find_package(
        self,
        package: str,
        *args: str,
        file_to_package_cache: list[tuple[str, str]] | None = None,
    ) -> Iterator[tuple[str, str | None]]:
        """Search for a file called <PackageName>Config.cmake or <lower-case-package-name>-config.cmake.

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
        try:
            query = make_cmake_config_query(package)
            yield file_to_package(query, file_to_package_cache=file_to_package_cache), version
        except ValueError:
            found_package = search_package(package)

            contents = run_command("apt-file", "list", found_package).decode("utf8")
            if file_to_package_cache is not None:
                for line in contents.split("\n"):
                    if ": " not in line:
                        continue
                    package_i, filename_i = line.split(": ")
                    file_to_package_cache.append((package_i, filename_i))
                    diagnostic(Severity.DEBUG, __name__, "Found package: %s %s", package_i, filename_i)

            yield found_package, version

    def _pkg_check_modules(
        self,
        prefix: str,  # noqa: ARG002
        *args: str,
        file_to_package_cache: dict[str, str] | None = None,
    ) -> Iterator[tuple[str, str | None]]:
        """Check for given modules and use the first working one.

        pkg_check_modules(<prefix>
                  [REQUIRED] [QUIET]
                  [NO_CMAKE_PATH]
                  [NO_CMAKE_ENVIRONMENT_PATH]
                  [IMPORTED_TARGET [GLOBAL]]
                  <moduleSpec> [<moduleSpec>...])

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
            query = make_pkg_config_query([module_name])
            yield (
                file_to_package(
                    query,
                    file_to_package_cache=list(file_to_package_cache.items()) if file_to_package_cache else None,
                ),
                version_range,
            )

    def _get_names(self, args: list[str], keywords: list[str]) -> list[str]:
        """Get the sequence of arguments after NAMES and until any of the keywords."""
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

    def _find_library(
        self, *args: str, file_to_package_cache: dict[str, str] | None = None
    ) -> Iterator[tuple[str, str | None]]:
        """Find library files.

        find_library (
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
            "PATH_SUFFIXESDOC",
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
        args = self._get_names(list(args[1:]), list(keywords))

        names = list({name if name.startswith("lib") else f"lib{name}" for name in args})
        query = make_library_query(names)
        yield (
            file_to_package(
                query,
                file_to_package_cache=list(file_to_package_cache.items()) if file_to_package_cache else None,
            ),
            None,
        )

    def _check_include_files(
        self,
        includes: str,  # noqa: ARG002
        variable: str,  # noqa: ARG002
        *args: str,
        file_to_package_cache: list[tuple[str, str]] | None = None,
    ) -> Iterator[tuple[str, str | None]]:
        """Check include files for CMake.

        CHECK_INCLUDE_FILES("<includes>" <variable> [LANGUAGE <language>])
        https://cmake.org/cmake/help/latest/module/CheckIncludeFiles.html
        """
        query = make_cmake_include_query(args[0].split(";"))
        yield file_to_package(query, file_to_package_cache=file_to_package_cache), None

    def _check_include_file(
        self,
        include_file: str,
        *args: str,  # noqa: ARG002
        file_to_package_cache: list[tuple[str, str]] | None = None,
    ) -> Iterator[tuple[str, str | None]]:
        """Check include file for CMake.

        CHECK_INCLUDE_FILE(<include> <variable> [<flags>])
        https://cmake.org/cmake/help/latest/module/CheckIncludeFile.html#module:CheckIncludeFile
        """
        query = make_cmake_include_query([include_file])
        yield file_to_package(query, file_to_package_cache=file_to_package_cache), None

    def _check_include_file_cxx(
        self, include_file: str, *args: str, file_to_package_cache: list[tuple[str, str]] | None = None
    ) -> Iterator[tuple[str, str | None]]:
        """Check include file for CXX in CMake.

        CHECK_INCLUDE_FILE_CXX(INCLUDE VARIABLE)
        https://cmake.org/cmake/help/v3.0/module/CheckIncludeFileCXX.html
        """
        yield from self._check_include_file(include_file, *args, file_to_package_cache=file_to_package_cache)

    def _find_path(
        self,
        var: str,  # noqa: ARG002
        *args: str,
        file_to_package_cache: list[tuple[str, str]] | None = None,
    ) -> Iterator[tuple[str, str | None]]:
        """Find path for CMake.

        find_path (<VAR> name1 [path1 path2 ...])
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
                query = make_path_query(name)
                yield (
                    file_to_package(query, file_to_package_cache=file_to_package_cache),
                    None,
                )
                break
            except Exception as e:  # noqa: BLE001
                diagnostic(Severity.DEBUG, __name__, e)

    def resolve(self, dependency: Dependency) -> Iterator[Package]:  # noqa: ARG002
        """Resolve a dependency to packages.

        CMake can only resolve from source, not by package name.
        """
        return iter(())

    def resolve_from_source(self, repo: SourceRepository, cache: PackageCache | None = None) -> SourcePackage | None:  # noqa: ARG002, C901, PLR0912, PLR0915
        """Resolve dependencies from source repository."""
        if not self.can_resolve_from_source(repo):
            return None
        if cmake_parsing is None:
            msg = "cmake resolver invoked without parse_cmake installed"
            raise RuntimeError(msg)
        path = repo.path
        diagnostic(Severity.INFO, __name__, "Getting dependencies for cmake repo %s", path)
        trace = _configure_trace(self.tool_path, path)

        package_name = None
        package_version = None

        file_to_package_cache: list[tuple[str, str]] = []
        deps: list[tuple[str, str | None]] = []
        bindings = {}
        try:
            for line in re.split(r"/.*\([0-9]+\):\s+", trace):
                if not line:
                    continue

                stripped_line = line.strip("\n")
                parsed: Iterable[cmake_parsing.BlankLine | cmake_parsing._Command] = ()

                try:
                    parsed = cmake_parsing.parse(stripped_line)
                except Exception as e:  # noqa: BLE001
                    diagnostic(Severity.DEBUG, __name__, "Parsing error: %s", e)
                    # ignore parsing exceptions for now

                for token in parsed:
                    try:
                        if isinstance(token, cmake_parsing.BlankLine):
                            continue
                        if isinstance(token, cmake_parsing._Command):  # noqa: SLF001
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
                            body: list[str] = [item for x in token.body for item in x.contents.split(";")]

                            if command not in ("set", "project"):
                                diagnostic(Severity.INFO, __name__, "Processing CMAKE command %s %s", command, body)

                            package_iter: Iterator[tuple[str, str | None]] = iter(())

                            # Dispatch over token name ...
                            if command == "project":
                                package_name = body[0]
                            elif command == "set":
                                # detect project version...
                                # TODO(@evandowning): Revisit the following type error: # noqa: TD003, FIX002
                                value: str | None = [*body, None][1]
                                if package_name is not None and body[0].lower() == f"{package_name}_version":
                                    package_version = value
                                bindings[body[0].upper()] = value
                            elif command == "find_package":
                                package_iter = self._find_package(*body, file_to_package_cache=file_to_package_cache)
                            elif command == "find_path":
                                package_iter = self._find_path(*body, file_to_package_cache=file_to_package_cache)
                            elif command == "find_library":
                                package_iter = self._find_library(
                                    *body,
                                    file_to_package_cache=dict(file_to_package_cache)
                                    if file_to_package_cache
                                    else None,
                                )
                            elif command == "pkg_check_modules":
                                package_iter = self._pkg_check_modules(
                                    *body,
                                    file_to_package_cache=dict(file_to_package_cache)
                                    if file_to_package_cache
                                    else None,
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
                                diagnostic(Severity.WARNING, __name__, "Not handled %s %s", token.name, body)
                            new_packages = tuple(package_iter)
                            deps.extend(new_packages)
                    except Exception as e:  # noqa: BLE001
                        diagnostic(Severity.DEBUG, __name__, e)
        except Exception as e:
            diagnostic(Severity.DEBUG, __name__, e)
            raise

        # remove "-dev"? and duplicates
        depsd: dict[str, str | None] = {}
        for name, version in deps:
            if name not in depsd or depsd[name] is None:
                depsd[name] = version
            elif version is not None and version != depsd[name]:
                # conflict
                diagnostic(
                    Severity.INFO,
                    __name__,
                    "Found a conflict in versions for %s (%s vs. %s). Setting '*'",
                    name,
                    version,
                    depsd[name],
                )
                depsd[name] = "*"

        if package_version is None:
            package_version = "0.0.0"
        if package_name is None:
            package_name = path.name
            diagnostic(
                Severity.WARNING, __name__, "Unable to determine package name for %s. Using %s", path, package_name
            )

        return SourcePackage(
            name=package_name,
            version=Version.coerce(package_version),
            source=self.name,
            dependencies=(
                Dependency(
                    package=name,
                    semantic_version=SimpleSpec((version is None and "*") or version),
                    source="ubuntu",
                )
                for name, version in depsd.items()
            ),
            source_repo=repo,
        )
