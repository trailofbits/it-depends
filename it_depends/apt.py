import functools
import gzip
import os
from pathlib import Path
import re
import logging
import shutil
import subprocess
from typing import Dict, Iterable, List, Optional, Set, Tuple
from urllib import request

from .docker import DockerContainer, InMemoryDockerfile
from .it_depends import APP_DIRS

logger = logging.getLogger(__name__)

CACHE_DIR = Path(APP_DIRS.user_cache_dir)

r''' Evan disapproves this
popdb = {}
@functools.lru_cache(maxsize=128)
def _popularity(packagename):
    """
    Downloads and uses popularity database
    """

    if arch not in ("amd64", "i386"):
        raise ValueError("Only amd64 and i386 supported")
    selected = None

    if not popdb:
        # TODO find better location https://pypi.org/project/appdirs/?

        dbfile = os.path.join(os.path.dirname(__file__), f"popcount.gz")
        if not os.path.exists(dbfile):
            logger.info("Popularity db not found. Downloading.")
            urllib.request.urlretrieve(
                "https://popcon.ubuntu.com/by_inst.gz",
                dbfile)

        logger.info("Popularity memory index not found. Building.")
        with gzip.open(dbfile, "rt") as contents:
            for line in contents.readlines():
                if line.startswith("#"):
                    continue
                print (line)
                re.compile(r"(?P<name>\S+)\s+")
                line.split(" ")
    print ("AAAAAAAAAAAAHH!")
    return 0
'''

all_packages: Optional[Tuple[str, ...]] = None


_container: Optional[DockerContainer] = None

_UBUNTU_NAME_MATCH: re.Pattern[str] = re.compile(r"^\s*name\s*=\s*\"ubuntu\"\s*$", flags=re.IGNORECASE)
_VERSION_ID_MATCH: re.Pattern[str] = re.compile(r"^\s*version_id\s*=\s*\"([^\"]+)\"\s*$", flags=re.IGNORECASE)


def is_running_ubuntu(check_version: Optional[str] = None) -> bool:
    """
    Tests whether the current system is running Ubuntu

    If `check_version` is not None, the specific version of Ubuntu is also tested.
    """
    os_release_path = Path("/etc/os-release")
    if not os_release_path.exists():
        return False
    is_ubuntu = False
    version: Optional[str] = None
    with open(os_release_path, "r") as f:
        for line in f.readlines():
            line = line.strip()
            is_ubuntu = is_ubuntu or bool(_UBUNTU_NAME_MATCH.match(line))
            if check_version is None:
                if is_ubuntu:
                    return True
            elif version is None:
                m = _VERSION_ID_MATCH.match(line)
                if m:
                    version = m.group(1)
            else:
                break
    return is_ubuntu and (check_version is None or version == check_version)


def run_command(*args: str) -> bytes:
    """
    Runs the given command in Ubuntu 20.04

    If the host system is not runnign Ubuntu 20.04, the command is run in Docker.

    """
    if shutil.which(args[0]) is None or not is_running_ubuntu(check_version="20.04"):
        # we do not have apt installed natively or are not running Ubuntu
        global _container
        if _container is None:
            with InMemoryDockerfile("""FROM ubuntu:20.04

RUN apt-get update && apt-get install -y apt-file && apt-file update
""") as dockerfile:
                _container = DockerContainer("trailofbits/it-depends-apt", dockerfile=dockerfile)
                _container.rebuild()
        p = _container.run(*args, interactive=False, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, rebuild=False)
    else:
        p = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    if p.returncode != 0:
        raise subprocess.CalledProcessError(p.returncode, cmd=f"{' '.join(args)}")
    return p.stdout



def get_apt_packages() -> Tuple[str, ...]:
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
                fr"^(lib)*{re.escape(package.lower())}(\-*([0-9]*)(\.*))*(\-dev)*$",
                apt_package):
            found_packages.append(apt_package)
    found_packages.sort(key=len, reverse=True)
    if not found_packages:
        raise ValueError(f"Package {package} not found in apt package list.")
    logger.info(
        f"Found {len(found_packages)} matching packages, Choosing {found_packages[0]}")
    return found_packages[0]


contents_db: Dict[str, List[str]] = {}
_loaded_dbs: Set[Path] = set()


@functools.lru_cache(maxsize=128)
def _file_to_package_contents(filename: str, arch: str = "amd64"):
    """
    Downloads and uses apt-file database directly
    # http://security.ubuntu.com/ubuntu/dists/focal-security/Contents-amd64.gz
    # http://security.ubuntu.com/ubuntu/dists/focal-security/Contents-i386.gz
    """
    if arch not in ("amd64", "i386"):
        raise ValueError("Only amd64 and i386 supported")
    selected = None

    dbfile = CACHE_DIR / f"Contents-{arch}.gz"
    if not dbfile.exists():
        request.urlretrieve(f"http://security.ubuntu.com/ubuntu/dists/focal-security/Contents-{arch}.gz", dbfile)
    if not dbfile in _loaded_dbs:
        logger.info("Rebuilding contents db")
        with gzip.open(str(dbfile), "rt") as contents:
            for line in contents.readlines():
                filename_i, *packages_i = re.split(r"\s+", line[:-1])
                assert(len(packages_i) > 0)
                contents_db.setdefault(filename_i, []).extend(packages_i)
        _loaded_dbs.add(dbfile)

    regex = re.compile("(.*/)+"+filename+"$")
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


@functools.lru_cache(maxsize=128)
def _file_to_package_apt_file(filename: str, arch: str = "amd64") -> str:
    if arch not in ("amd64", "i386"):
        raise ValueError("Only amd64 and i386 supported")
    logger.debug(f'Running [{" ".join(["apt-file", "-x", "search", filename])}]')
    contents = run_command("apt-file", "-x", "search", filename).decode("utf-8")
    db: Dict[str, str] = {}
    selected = None
    for line in contents.split("\n"):
        if not line:
            continue
        package_i, filename_i = line.split(": ")
        db[filename_i] = package_i
        if selected is None or len(selected[0]) > len(filename_i):
            selected = filename_i, package_i

    if selected:
        logger.info(
            f"Found {len(db)} matching packages for {filename}. Choosing {selected[1]}")
    else:
        raise ValueError(f"{filename} not found in apt-file")

    return selected[1]


@functools.lru_cache(maxsize=128)
def file_to_package(filename: str, arch: str = "amd64") -> str:
    filename = f"^{Path(filename).absolute()}$"
    return _file_to_package_apt_file(filename, arch=arch)


def cached_file_to_package(pattern: str, file_to_package_cache: Optional[List[Tuple[str, str]]] = None) -> str:
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
