"""Ubuntu APT package management module."""

from __future__ import annotations

import functools
import gzip
import logging
import re
from pathlib import Path
from threading import Lock
from urllib import request

from it_depends.it_depends import APP_DIRS

from .docker import run_command

logger = logging.getLogger(__name__)
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


@functools.lru_cache(maxsize=5242880)
def file_to_packages(filename: str, arch: str = "amd64") -> list[str]:
    """Get packages that provide a specific file."""
    if arch not in ("amd64", "i386"):
        error_msg = "Only amd64 and i386 supported"
        raise ValueError(error_msg)
    logger.debug("Running [apt-file -x search %s]", filename)
    contents = run_command("apt-file", "-x", "search", filename).decode("utf-8")
    selected: list[str] = []
    for line in contents.split("\n"):
        if not line:
            continue
        package_i, _ = line.split(": ")
        selected.append(package_i)
    return sorted(selected)


def file_to_package(filename: str, arch: str = "amd64") -> str:
    """Get the package that provides a specific file."""
    packages = file_to_packages(filename, arch)
    if packages:
        _, result = min((len(pkg), pkg) for pkg in packages)
        logger.info("Found %d matching packages for %s. Choosing %s", len(packages), filename, result)
        return result
    error_msg = f"{filename} not found in apt-file"
    raise ValueError(error_msg)


def cached_file_to_package(pattern: str, file_to_package_cache: list[tuple[str, str]] | None = None) -> str:
    """Get package for a file pattern with caching support."""
    # file_to_package_cache contains all the files that are provided be previous
    # dependencies. If a file pattern is already sastified by current files
    # use the package already included as a dependency
    if file_to_package_cache is not None:
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
