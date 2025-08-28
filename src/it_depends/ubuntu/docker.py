"""Ubuntu Docker container management module."""

from __future__ import annotations

import logging
import re
import subprocess
from functools import lru_cache
from pathlib import Path
from re import Pattern
from threading import Lock

from it_depends.docker import DockerContainer, InMemoryDockerfile

_container: DockerContainer | None = None
_UBUNTU_LOCK: Lock = Lock()

_UBUNTU_NAME_MATCH: Pattern[str] = re.compile(r"^\s*name\s*=\s*\"ubuntu\"\s*$", flags=re.IGNORECASE)
_VERSION_ID_MATCH: Pattern[str] = re.compile(r"^\s*version_id\s*=\s*\"([^\"]+)\"\s*$", flags=re.IGNORECASE)

logger = logging.getLogger(__name__)


@lru_cache(maxsize=4)
def is_running_ubuntu(check_version: str | None = None) -> bool:
    """Test whether the current system is running Ubuntu.

    If `check_version` is not None, the specific version of Ubuntu is also tested.
    """
    os_release_path = Path("/etc/os-release")
    if not os_release_path.exists():
        return False
    is_ubuntu = False
    version: str | None = None
    with os_release_path.open() as f:
        for line_content in f:
            stripped_line = line_content.strip()
            is_ubuntu = is_ubuntu or bool(_UBUNTU_NAME_MATCH.match(stripped_line))
            if check_version is None:
                if is_ubuntu:
                    return True
            elif version is None:
                m = _VERSION_ID_MATCH.match(stripped_line)
                if m:
                    version = m.group(1)
            else:
                break
    return is_ubuntu and (check_version is None or version == check_version)


def run_command(*args: str) -> bytes:
    """Run the given command in Ubuntu 20.04.

    If the host system is not running Ubuntu 20.04, the command is run in Docker.
    """
    with _UBUNTU_LOCK:
        global _container  # noqa: PLW0603
        if _container is None:
            with InMemoryDockerfile(
                """FROM ubuntu:20.04

RUN apt-get update && apt-get install -y apt-file && apt-file update
"""
            ) as dockerfile:
                _container = DockerContainer("trailofbits/it-depends-apt", dockerfile=dockerfile)
                _container.rebuild()
    logger.debug("running %s in Docker", " ".join(args))
    p = _container.run(
        *args,
        interactive=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        rebuild=False,
    )
    if p.returncode != 0:
        raise subprocess.CalledProcessError(p.returncode, cmd=" ".join(args))
    return p.stdout
