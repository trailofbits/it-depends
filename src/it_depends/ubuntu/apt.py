import functools
import gzip
from pathlib import Path
import re
import logging
from threading import Lock
from typing import Dict, List, Optional, Set, Tuple
from urllib import request

from ..it_depends import APP_DIRS
from .docker import run_command

logger = logging.getLogger(__name__)
all_packages: Optional[Tuple[str, ...]] = None
_APT_LOCK: Lock = Lock()


def get_apt_packages() -> Tuple[str, ...]:
    with _APT_LOCK:
        global all_packages
        if all_packages is None:
            logger.info("Rebuilding global apt package list.")
            raw_packages = run_command("apt", "list").decode("utf-8")
            all_packages = tuple(x.split("/")[0] for x in raw_packages.splitlines() if x)

            logger.info(f"Global apt package count {len(all_packages)}")
        return all_packages


def search_package(package: str) -> str:
    found_packages: List[str] = []
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
        raise ValueError(f"Package {package} not found in apt package list.")
    logger.info(f"Found {len(found_packages)} matching packages, Choosing {found_packages[0]}")
    return found_packages[0]


contents_db: Dict[str, List[str]] = {}
_loaded_dbs: Set[Path] = set()


@functools.lru_cache(maxsize=5242880)
def _file_to_package_contents(filename: str, arch: str = "amd64"):
    """
    Downloads and uses apt-file database directly
    # http://security.ubuntu.com/ubuntu/dists/focal-security/Contents-amd64.gz
    # http://security.ubuntu.com/ubuntu/dists/focal-security/Contents-i386.gz
    """
    if arch not in ("amd64", "i386"):
        raise ValueError("Only amd64 and i386 supported")
    selected = None

    dbfile = Path(APP_DIRS.user_cache_dir) / f"Contents-{arch}.gz"
    if not dbfile.exists():
        request.urlretrieve(
            f"http://security.ubuntu.com/ubuntu/dists/focal-security/Contents-{arch}.gz",
            dbfile,
        )
    if not dbfile in _loaded_dbs:
        logger.info("Rebuilding contents db")
        with gzip.open(str(dbfile), "rt") as contents:
            for line in contents.readlines():
                filename_i, *packages_i = re.split(r"\s+", line[:-1])
                assert len(packages_i) > 0
                contents_db.setdefault(filename_i, []).extend(packages_i)
        _loaded_dbs.add(dbfile)

    regex = re.compile("(.*/)+" + filename + "$")
    matches = 0
    for (filename_i, packages_i) in contents_db.items():
        if regex.match(filename_i):
            matches += 1
            for package_i in packages_i:
                if selected is None or len(selected[0]) > len(filename_i):
                    selected = filename_i, package_i
    if selected:
        logger.info(f"Found {matches} matching packages for {filename}. Choosing {selected[1]}")
    else:
        raise ValueError(f"{filename} not found in Contents database")
    return selected[1]


@functools.lru_cache(maxsize=5242880)
def file_to_packages(filename: str, arch: str = "amd64") -> List[str]:
    if arch not in ("amd64", "i386"):
        raise ValueError("Only amd64 and i386 supported")
    logger.debug(f'Running [{" ".join(["apt-file", "-x", "search", filename])}]')
    contents = run_command("apt-file", "-x", "search", filename).decode("utf-8")
    selected: List[str] = []
    for line in contents.split("\n"):
        if not line:
            continue
        package_i, _ = line.split(": ")
        selected.append(package_i)
    return sorted(selected)


def file_to_package(filename: str, arch: str = "amd64") -> str:
    packages = file_to_packages(filename, arch)
    if packages:
        _, result = min((len(pkg), pkg) for pkg in packages)
        logger.info(f"Found {len(packages)} matching packages for {filename}. Choosing {result}")
        return result
    else:
        raise ValueError(f"{filename} not found in apt-file")


def cached_file_to_package(
    pattern: str, file_to_package_cache: Optional[List[Tuple[str, str]]] = None
) -> str:
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
