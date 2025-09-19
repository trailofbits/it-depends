"""Docker container management for dependency resolution."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from typing import TYPE_CHECKING, Self

if TYPE_CHECKING:
    from collections.abc import Iterable
from pathlib import Path
from tempfile import mkdtemp

import docker
from docker.errors import DockerException
from docker.errors import NotFound as ImageNotFound

if TYPE_CHECKING:
    from docker.models.images import Image
from tqdm import tqdm

from . import version as it_depends_version


def _discover_podman_socket() -> str | None:
    """Try to discover a Podman socket.

    Discovery is performed in this order:

    * If the user is non-root, rootless Podman
    * If the user is root, rooted Podman
    """
    euid = os.geteuid()
    if euid != 0:
        # Non-root: use XDG_RUNTIME_DIR to try and find the user's Podman socket,
        # falling back on the systemd-enforced default.
        # Ref: https://docs.podman.io/en/latest/markdown/podman-system-service.1.html
        runtime_dir = Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{euid}/"))
        if not runtime_dir.is_dir():
            return None

        sock_path = runtime_dir / "podman/podman.sock"
    else:
        # Root: check for /run/podman/podman.sock and nothing else.
        sock_path = Path("/run/podman/podman.sock")

    if not sock_path.is_socket():
        return None

    return f"unix://{sock_path}"


class Dockerfile:
    """Dockerfile management."""

    def __init__(self, path: Path) -> None:
        """Initialize Dockerfile from path."""
        self._path: Path = path
        self._len: int | None = None
        self._line_offsets: dict[int, int] = {}

    @property
    def path(self) -> Path:
        """Get Dockerfile path."""
        return self._path

    @path.setter
    def path(self, new_path: Path) -> None:
        """Set Dockerfile path."""
        self._path = new_path
        self._len = None
        self._line_offsets = {}

    def __enter__(self) -> Self:
        """Enter context manager."""
        return self

    def __exit__(self, exc_type: object, exc_val: object, exc_tb: object) -> None:
        """Exit context manager."""

    def exists(self) -> bool:
        """Check if Dockerfile exists."""
        return self.path.exists()

    def dir(self) -> Path:
        """Get Dockerfile directory."""
        return self.path.parent

    def __len__(self) -> int:
        """Return the number of lines in the file."""
        if self._len is None:
            self._len = 0
            self._line_offsets[0] = 0  # line 0 starts at offset 0
            offset = 0
            with self.path.open("rb") as f:
                while True:
                    chunk = f.read(1)
                    if len(chunk) == 0:
                        break
                    if chunk == b"\n":
                        self._len += 1
                        self._line_offsets[self._len] = offset + 1
                    offset += 1
        return self._len

    def get_line(self, step_command: str, starting_line: int = 0) -> int | None:
        """Return the line number of the associated step command."""
        if self._len is None:
            # we need to call __len__ to set self._line_offsets
            _ = len(self)
        if starting_line not in self._line_offsets:
            return None
        with self.path.open() as f:
            f.seek(self._line_offsets[starting_line])
            line_offset = 0
            while True:
                line = f.readline()
                if line == "":
                    break
                if line == step_command:
                    return starting_line + line_offset
                line_offset += 1
            return None


class InMemoryFile:
    """In-memory file for Docker builds."""

    def __init__(self, filename: str, content: bytes) -> None:
        """Initialize in-memory file."""
        self.filename: str = filename
        self.content: bytes = content


class InMemoryDockerfile(Dockerfile):
    """In-memory Dockerfile for builds."""

    def __init__(self, content: str, local_files: Iterable[InMemoryFile] = ()) -> None:
        """Initialize in-memory Dockerfile."""
        super().__init__(Path.cwd() / "dummy")  # Dummy path, will be overridden
        self.content: str = content
        self.local_files: list[InMemoryFile] = list(local_files)
        self._entries: int = 0
        self._tmpdir: Path | None = None

    @property
    def path(self) -> Path:
        """Get Dockerfile path."""
        path = super().path
        if path is None:
            msg = "InMemoryDockerfile only has a valid path when inside of its context manager"
            raise ValueError(msg)
        return path

    @path.setter
    def path(self, new_path: Path) -> None:
        """Set Dockerfile path."""
        self._path = new_path

    def __enter__(self) -> Self:
        """Enter context manager."""
        self._entries += 1
        if self._entries == 1:
            self._tmpdir = Path(mkdtemp())
            for file in self.local_files:
                with (self._tmpdir / file.filename).open("wb") as f:
                    f.write(file.content)
            self.path = self._tmpdir / "Dockerfile"
            with self.path.open("w") as d:
                d.write(self.content)
        return self

    def __exit__(self, exc_type: object, exc_val: object, exc_tb: object) -> None:
        """Exit context manager."""
        self._entries -= 1
        if self._entries == 0:
            if self.path is not None:
                self.path.unlink()
            if self._tmpdir is not None:
                shutil.rmtree(self._tmpdir)
            self.path = None  # type: ignore[assignment]


class DockerContainer:
    """Docker container for dependency resolution."""

    def __init__(
        self,
        image_name: str,
        dockerfile: Dockerfile | None = None,
        tag: str | None = None,
    ) -> None:
        """Initialize Docker container."""
        self.image_name: str = image_name
        if tag is None:
            self.tag: str = it_depends_version()
        else:
            self.tag = tag
        self._client: docker.DockerClient | None = None
        self.dockerfile: Dockerfile | None = dockerfile

    def run(  # noqa: C901, PLR0912, PLR0913
        self,
        *args: str,
        check_existence: bool = True,
        rebuild: bool = True,
        build_if_necessary: bool = True,
        remove: bool = True,
        interactive: bool = True,
        mounts: Iterable[tuple[str | Path, str | Path]] | None = None,
        privileged: bool = False,
        env: dict[str, str] | None = None,
        stdin: int | None = None,
        stdout: int | None = None,
        stderr: int | None = None,
        cwd: str | None = None,
    ) -> subprocess.CompletedProcess[bytes]:
        """Run command in Docker container."""
        if rebuild:
            self.rebuild()
        elif check_existence and not self.exists():
            if build_if_necessary:
                if self.dockerfile is not None and self.dockerfile.exists():
                    self.rebuild()
                else:
                    self.pull()
                if not self.exists():
                    msg = f"{self.name} does not exist!"
                    raise ValueError(msg)
            else:
                msg = f"{self.name} does not exist! Re-run with `build_if_necessary=True` to automatically build it."
                raise ValueError(msg)
        if cwd is None:
            cwd = str(Path.cwd())

        # Call out to the actual Docker command instead of the Python API because it has better support for interactive
        # TTYs

        if interactive and (stdin is not None or stdout is not None or stderr is not None):
            msg = "if `interactive == True`, all of `stdin`, `stdout`, and `stderr` must be `None`"
            raise ValueError(msg)

        cmd_args = [str(Path("/usr") / "bin" / "env"), "docker", "run"]

        if interactive:
            cmd_args.append("-it")

        if remove:
            cmd_args.append("--rm")

        if mounts is not None:
            for source, target in mounts:
                cmd_args.append("-v")
                source_path = Path(source) if not isinstance(source, Path) else source
                source_path = source_path.absolute()
                cmd_args.append(f"{source_path!s}:{target!s}:cached")

        if env is not None:
            for k, v in env.items():
                cmd_args.append("-e")
                escaped_value = v.replace('"', '\\"')
                cmd_args.append(f"{k}={escaped_value}")

        if privileged:
            cmd_args.append("--privileged=true")

        cmd_args.append(self.name)

        cmd_args.extend(args)

        if interactive:
            returncode = subprocess.call(cmd_args, cwd=cwd, stdout=sys.stderr)  # noqa: S603
            return subprocess.CompletedProcess(cmd_args, returncode)
        return subprocess.run(cmd_args, check=False, stdin=stdin, stdout=stdout, stderr=stderr, cwd=cwd)  # noqa: S603

    @property
    def name(self) -> str:
        """Get container name."""
        return f"{self.image_name}:{self.tag}"

    @property
    def client(self) -> docker.DockerClient:
        """Get Docker client."""
        if self._client is None:
            self._client = docker.from_env()
        return self._client

    def exists(self) -> Image | None:
        """Check if container image exists."""
        for image in self.client.images.list():
            if self.name in image.tags:
                return image
        return None

    def pull(self, *, latest: bool = False) -> Image:
        """Pull container image."""
        # However, that doesn't include progress bars. So call the `docker` command instead:
        name = f"{self.image_name}:{[self.tag, 'latest'][latest]}"
        try:
            subprocess.check_call(["docker", "pull", name])  # noqa: S603, S607
            for image in self.client.images.list():
                if name in image.tags:
                    return image
        except subprocess.CalledProcessError:
            pass
        raise ImageNotFound(name)

    def rebuild(self, *, nocache: bool = False) -> None:  # noqa: C901
        """Rebuild container image."""
        if self.dockerfile is None:
            _ = self.pull()
            return
        if not self.dockerfile.exists():
            msg = "Could not find the Dockerfile."
            raise ValueError(msg)
        # use the low-level APIClient so we can get streaming build status
        try:
            sock = _discover_podman_socket()
            cli = docker.APIClient(base_url=sock)
        except DockerException as e:
            msg = f"Could not connect to socket: sock={sock} {e}"
            raise ValueError(msg) from e
        with tqdm(desc="Archiving the build directory", unit=" steps", leave=False) as t:
            last_line = 0
            last_step = None
            for raw_line in cli.build(
                path=str(self.dockerfile.dir()),
                rm=True,
                tag=self.name,
                nocache=nocache,
                forcerm=True,
            ):
                t.desc = f"Building {self.name}"
                for line in raw_line.split(b"\n"):
                    try:
                        parsed_line = json.loads(line)
                    except json.decoder.JSONDecodeError:
                        continue
                    if "stream" in parsed_line:
                        m = re.match(
                            r"^Step\s+(\d+)(/(\d+))?\s+:\s+(.+)$",
                            parsed_line["stream"],
                            re.MULTILINE,
                        )
                        if m:
                            if m.group(3):
                                # Docker told us the total number of steps!
                                total_steps = int(m.group(3))
                                current_step = int(m.group(1))
                                if last_step is None:
                                    t.total = total_steps
                                    last_step = 0
                                t.update(current_step - last_step)
                                last_step = current_step
                            else:
                                # Docker didn't tell us the total number of steps, so infer it from our line
                                # number in the Dockerfile
                                t.total = len(self.dockerfile)
                                new_line = self.dockerfile.get_line(m.group(4), starting_line=last_line)
                                if new_line is not None:
                                    t.update(new_line - last_line)
                                    last_line = new_line
                        t.write(parsed_line["stream"].replace("\n", "").strip())
