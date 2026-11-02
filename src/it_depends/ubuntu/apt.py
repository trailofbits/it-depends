"""Ubuntu APT package management module."""

from __future__ import annotations

import functools
import gzip
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from urllib import request

from it_depends.it_depends import APP_DIRS

from .docker import run_command

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AptFileQuery:
    """Query for optimized apt-file search.

    Attributes:
        search_terms: Simple terms for case-insensitive apt-file search.
        filter_regex: Python regex pattern for post-filtering results.

    """

    search_terms: tuple[str, ...]
    filter_regex: str


def make_library_query(lib_names: list[str]) -> AptFileQuery:
    """Create query for library files (.so/.a).

    Args:
        lib_names: Library names (with or without 'lib' prefix).

    Returns:
        AptFileQuery with search terms and filter regex.

    """
    normalized = [n if n.startswith("lib") else f"lib{n}" for n in lib_names]
    escaped = [re.escape(n) for n in normalized]
    return AptFileQuery(
        search_terms=tuple(normalized),
        filter_regex=rf"({'|'.join(escaped)})(\.so[0-9\.]*|\.a)$",
    )


def make_cmake_include_query(headers: list[str]) -> AptFileQuery:
    """Create query for cmake header files.

    Args:
        headers: Header file names (e.g., ["pthread.h", "stdlib.h"]).

    Returns:
        AptFileQuery with search terms and filter regex.

    """
    escaped = [re.escape(h) for h in headers]
    return AptFileQuery(
        search_terms=tuple(headers),
        filter_regex=r"include/(.*/)*(" + "|".join(escaped) + r")$",
    )


def make_pkg_config_query(modules: list[str]) -> AptFileQuery:
    """Create query for pkg-config .pc files.

    Args:
        modules: Module names without .pc extension.

    Returns:
        AptFileQuery with search terms and filter regex.

    """
    pc_files = [f"{m}.pc" for m in modules]
    escaped = [re.escape(f) for f in pc_files]
    return AptFileQuery(
        search_terms=tuple(pc_files),
        filter_regex=rf"({'|'.join(escaped)})$",
    )


def make_cmake_config_query(package: str) -> AptFileQuery:
    """Create query for CMake config files.

    Looks for: <name>.pc, <name>Config.cmake, <name>-config.cmake

    Args:
        package: The CMake package name.

    Returns:
        AptFileQuery with search terms and filter regex.

    """
    escaped = re.escape(package)
    lower_escaped = re.escape(package.lower())
    return AptFileQuery(
        search_terms=(package, f"{package}Config.cmake", f"{package.lower()}-config.cmake"),
        filter_regex=rf"({escaped}\.pc|{escaped}Config\.cmake|{lower_escaped}-config\.cmake)$",
    )


def make_autotools_include_query(headers: list[str]) -> AptFileQuery:
    """Create query for autotools header files.

    Args:
        headers: Header file names (e.g., ["pthread.h", "stdlib.h"]).

    Returns:
        AptFileQuery with search terms and filter regex.

    """
    escaped = [re.escape(h) for h in headers]
    return AptFileQuery(
        search_terms=tuple(headers),
        filter_regex=rf"({'|'.join(escaped)})$",
    )


def make_path_query(name: str) -> AptFileQuery:
    """Create query for generic path search.

    Args:
        name: The filename to search for.

    Returns:
        AptFileQuery with search terms and filter regex.

    """
    return AptFileQuery(
        search_terms=(name,),
        filter_regex=rf"{re.escape(name)}$",
    )


all_packages: tuple[str, ...] | None = None
_APT_LOCK: Lock = Lock()


def get_apt_packages() -> tuple[str, ...]:
    """Get all available APT packages."""
    with _APT_LOCK:
        global all_packages  # noqa: PLW0603
        if all_packages is None:
            logger.info("Rebuilding global apt package list.")
            raw_packages = run_command("apt", "list").decode("utf-8")
            all_packages = tuple(x.split("/")[0] for x in raw_packages.splitlines() if x)

            logger.info("Global apt package count %d", len(all_packages))
        return all_packages


def search_package(package: str) -> str:
    """Search for a package by name."""
    found_packages: list[str] = []
    for apt_package in get_apt_packages():
        if package.lower() not in apt_package:
            continue
        if re.match(
            rf"^(lib)*{re.escape(package.lower())}(\-*([0-9]*)(\.*))*(\-dev)*$",
            apt_package,
        ):
            found_packages.append(apt_package)
    found_packages.sort(key=len, reverse=True)
    if not found_packages:
        error_msg = f"Package {package} not found in apt package list."
        raise ValueError(error_msg)
    logger.info("Found %d matching packages, Choosing %s", len(found_packages), found_packages[0])
    return found_packages[0]


contents_db: dict[str, list[str]] = {}
_loaded_dbs: set[Path] = set()


@functools.lru_cache(maxsize=5242880)
def _file_to_package_contents(filename: str, arch: str = "amd64") -> str:  # noqa: C901
    """Download and use apt-file database directly.

    # http://security.ubuntu.com/ubuntu/dists/focal-security/Contents-amd64.gz
    # http://security.ubuntu.com/ubuntu/dists/focal-security/Contents-i386.gz
    """
    if arch not in ("amd64", "i386"):
        error_msg = "Only amd64 and i386 supported"
        raise ValueError(error_msg)
    selected = None

    dbfile = Path(APP_DIRS.user_cache_dir) / f"Contents-{arch}.gz"
    if not dbfile.exists():
        request.urlretrieve(
            f"http://security.ubuntu.com/ubuntu/dists/focal-security/Contents-{arch}.gz",
            dbfile,
        )
    if dbfile not in _loaded_dbs:
        logger.info("Rebuilding contents db")
        with gzip.open(str(dbfile), "rt") as contents:
            for line in contents.readlines():
                filename_i, *packages_i = re.split(r"\s+", line[:-1])
                if len(packages_i) <= 0:
                    continue
                contents_db.setdefault(filename_i, []).extend(packages_i)
        _loaded_dbs.add(dbfile)

    regex = re.compile("(.*/)+" + filename + "$")
    matches = 0
    for filename_i, packages_i in contents_db.items():
        if regex.match(filename_i):
            matches += 1
            for package_i in packages_i:
                if selected is None or len(selected[0]) > len(filename_i):
                    selected = filename_i, package_i
    if selected:
        logger.info("Found %d matching packages for %s. Choosing %s", matches, filename, selected[1])
    else:
        error_msg = f"{filename} not found in Contents database"
        raise ValueError(error_msg)
    return selected[1]


def _file_to_packages_optimized(
    query: AptFileQuery,
    arch: str = "amd64",  # noqa: ARG001
) -> list[str]:
    """Optimized apt-file search using case-insensitive mode with post-filtering.

    Args:
        query: AptFileQuery containing search terms and filter regex.
        arch: Architecture to search (amd64 or i386). Reserved for future use.

    Returns:
        Sorted list of matching package names.

    """
    all_packages: set[str] = set()
    regex = re.compile(query.filter_regex)

    for term in query.search_terms:
        logger.debug("Running [apt-file -i search %s]", term)
        try:
            contents = run_command("apt-file", "-i", "search", term).decode("utf-8")
        except Exception:  # noqa: BLE001
            logger.debug("apt-file search failed for term: %s", term)
            continue

        for line in contents.split("\n"):
            if not line or ": " not in line:
                continue
            package_name, file_path = line.split(": ", 1)
            if regex.search(file_path):
                all_packages.add(package_name)

    logger.debug("Finished apt-file, found %d packages", len(all_packages))
    return sorted(all_packages)


@functools.lru_cache(maxsize=5242880)
def file_to_packages(filename: str | AptFileQuery, arch: str = "amd64") -> list[str]:
    """Get packages that provide a specific file.

    Args:
        filename: Either a regex pattern string or an AptFileQuery for optimized search.
        arch: Architecture to search (amd64 or i386).

    Returns:
        Sorted list of package names that provide matching files.

    """
    if arch not in ("amd64", "i386"):
        error_msg = "Only amd64 and i386 supported"
        raise ValueError(error_msg)

    if isinstance(filename, AptFileQuery):
        return _file_to_packages_optimized(filename, arch)

    # Legacy regex mode for backward compatibility
    logger.debug("Running [apt-file -x search %s]", filename)
    contents = run_command("apt-file", "-x", "search", filename).decode("utf-8")
    selected: list[str] = []
    for line in contents.split("\n"):
        if not line:
            continue
        package_i, _ = line.split(": ")
        selected.append(package_i)
    logger.debug("Finished running apt-file")
    return sorted(selected)


def file_to_package(filename: str | AptFileQuery, arch: str = "amd64") -> str:
    """Get the package that provides a specific file.

    Args:
        filename: Either a regex pattern string or an AptFileQuery for optimized search.
        arch: Architecture to search (amd64 or i386).

    Returns:
        The shortest package name that provides a matching file.

    Raises:
        ValueError: If no matching package is found.

    """
    packages = file_to_packages(filename, arch)
    if packages:
        _, result = min((len(pkg), pkg) for pkg in packages)
        logger.info("Found %d matching packages for %s. Choosing %s", len(packages), filename, result)
        return result
    error_msg = f"{filename} not found in apt-file"
    raise ValueError(error_msg)


def cached_file_to_package(
    pattern: str | AptFileQuery,
    file_to_package_cache: list[tuple[str, str]] | None = None,
) -> str:
    """Get package for a file pattern with caching support.

    Args:
        pattern: Either a regex pattern string or an AptFileQuery.
        file_to_package_cache: Cache of (package, filename) tuples from previous deps.

    Returns:
        The package name providing the file.

    Raises:
        ValueError: If no matching package is found.

    """
    # file_to_package_cache contains all the files that are provided by previous
    # dependencies. If a file pattern is already satisfied by current files
    # use the package already included as a dependency
    if file_to_package_cache is not None:
        if isinstance(pattern, AptFileQuery):
            regex = re.compile(pattern.filter_regex)
        else:
            regex = re.compile("(.*/)+" + pattern + "$")
        for package_i, filename_i in file_to_package_cache:
            if regex.match(filename_i):
                return package_i

    package = file_to_package(pattern)

    # a new package is chosen add all the files it provides to our cache
    # uses `apt-file` command line tool
    if file_to_package_cache is not None:
        contents = run_command("apt-file", "list", package).decode("utf-8")
        for line in contents.split("\n"):
            if ":" not in line:
                break
            package_i, filename_i = line.split(": ")
            file_to_package_cache.append((package_i, filename_i))

    return package
