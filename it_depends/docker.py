import json
import re
import shutil
import subprocess
import sys
import os
from pathlib import Path
from tempfile import mkdtemp
from tqdm import tqdm
from typing import Dict, Iterable, List, Optional, Tuple, Union

import docker
from docker.errors import NotFound as ImageNotFound, DockerException
from docker.models.images import Image

from . import version as it_depends_version


def _discover_podman_socket():
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
    def __init__(self, path: Path):
        self._path: Path = path
        self._len: Optional[int] = None
        self._line_offsets: Dict[int, int] = {}

    @property
    def path(self) -> Path:
        return self._path

    @path.setter
    def path(self, new_path: Path):
        self._path = new_path
        self._len = None
        self._line_offsets = {}

    def __enter__(self) -> "Dockerfile":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        pass

    def exists(self) -> bool:
        return self.path.exists()

    def dir(self) -> Path:
        return self.path.parent

    def __len__(self) -> int:
        """Returns the number of lines in the file"""
        if self._len is None:
            self._len = 0
            self._line_offsets[0] = 0  # line 0 starts at offset 0
            offset = 0
            with open(self.path, "rb") as f:
                while True:
                    chunk = f.read(1)
                    if len(chunk) == 0:
                        break
                    elif chunk == b"\n":
                        self._len += 1
                        self._line_offsets[self._len] = offset + 1
                    offset += 1
        return self._len

    def get_line(self, step_command: str, starting_line: int = 0) -> Optional[int]:
        """Returns the line number of the associated step command"""
        if self._len is None:
            # we need to call __len__ to set self._line_offsets
            _ = len(self)
        if starting_line not in self._line_offsets:
            return None
        with open(self.path, "r") as f:
            f.seek(self._line_offsets[starting_line])
            line_offset = 0
            while True:
                line = f.readline()
                if line == "":
                    break
                elif line == step_command:
                    return starting_line + line_offset
                line_offset += 1
            return None


class InMemoryFile:
    def __init__(self, filename: str, content: bytes):
        self.filename: str = filename
        self.content: bytes = content


class InMemoryDockerfile(Dockerfile):
    def __init__(self, content: str, local_files: Iterable[InMemoryFile] = ()):
        super().__init__(None)  # type: ignore
        self.content: str = content
        self.local_files: List[InMemoryFile] = list(local_files)
        self._entries: int = 0
        self._tmpdir: Optional[Path] = None

    @Dockerfile.path.getter  # type: ignore
    def path(self) -> Path:
        path = super().path
        if path is None:
            raise ValueError(
                "InMemoryDockerfile only has a valid path when inside of its context manager"
            )
        return path

    def __enter__(self) -> "InMemoryDockerfile":
        self._entries += 1
        if self._entries == 1:
            self._tmpdir = Path(mkdtemp())
            for file in self.local_files:
                with open(self._tmpdir / file.filename, "wb") as f:
                    f.write(file.content)
            self.path = self._tmpdir / "Dockerfile"
            with open(self.path, "w") as d:
                d.write(self.content)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self._entries -= 1
        if self._entries == 0:
            self.path.unlink()
            shutil.rmtree(self._tmpdir)
            self.path = None  # type: ignore


class DockerContainer:
    def __init__(
        self,
        image_name: str,
        dockerfile: Optional[Dockerfile] = None,
        tag: Optional[str] = None,
    ):
        self.image_name: str = image_name
        if tag is None:
            self.tag: str = it_depends_version()
        else:
            self.tag = tag
        self._client: Optional[docker.DockerClient] = None
        self.dockerfile: Optional[Dockerfile] = dockerfile

    def run(
        self,
        *args: str,
        check_existence: bool = True,
        rebuild: bool = True,
        build_if_necessary: bool = True,
        remove: bool = True,
        interactive: bool = True,
        mounts: Optional[Iterable[Tuple[Union[str, Path], Union[str, Path]]]] = None,
        privileged: bool = False,
        env: Optional[Dict[str, str]] = None,
        stdin=None,
        stdout=None,
        stderr=None,
        cwd=None,
    ):
        if rebuild:
            self.rebuild()
        elif check_existence and not self.exists():
            if build_if_necessary:
                if self.dockerfile is not None and self.dockerfile.exists():
                    self.rebuild()
                else:
                    self.pull()
                if not self.exists():
                    raise ValueError(f"{self.name} does not exist!")
            else:
                raise ValueError(
                    f"{self.name} does not exist! Re-run with `build_if_necessary=True` to automatically "
                    "build it."
                )
        if cwd is None:
            cwd = str(Path.cwd())

        # Call out to the actual Docker command instead of the Python API because it has better support for interactive
        # TTYs

        if interactive and (stdin is not None or stdout is not None or stderr is not None):
            raise ValueError(
                "if `interactive == True`, all of `stdin`, `stdout`, and `stderr` must be `None`"
            )

        cmd_args = [str(Path("/usr") / "bin" / "env"), "docker", "run"]

        if interactive:
            cmd_args.append("-it")

        if remove:
            cmd_args.append("--rm")

        if mounts is not None:
            for source, target in mounts:
                cmd_args.append("-v")
                if not isinstance(source, Path):
                    source = Path(source)
                source = source.absolute()
                cmd_args.append(f"{source!s}:{target!s}:cached")

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
            return subprocess.call(cmd_args, cwd=cwd, stdout=sys.stderr)
        else:
            return subprocess.run(cmd_args, stdin=stdin, stdout=stdout, stderr=stderr, cwd=cwd)

        # self.client.containers.run(self.name, args, remove=remove, mounts=[
        #     Mount(target=str(target), source=str(source), consistency="cached") for source, target in mounts
        # ])

    @property
    def name(self) -> str:
        return f"{self.image_name}:{self.tag}"

    @property
    def client(self) -> docker.DockerClient:
        if self._client is None:
            self._client = docker.from_env()
        return self._client

    def exists(self) -> Optional[Image]:
        for image in self.client.images.list():
            if self.name in image.tags:
                return image
        return None

    def pull(self, latest: bool = False) -> Image:
        # We could use the Python API to pull, like this:
        #     return self.client.images.pull(self.image_name, tag=[self.tag, None][latest])
        # However, that doesn't include progress bars. So call the `docker` command instead:
        name = f"{self.image_name}:{[self.tag, 'latest'][latest]}"
        try:
            subprocess.check_call(["docker", "pull", name])
            for image in self.client.images.list():
                if name in image.tags:
                    return image
        except subprocess.CalledProcessError:
            pass
        raise ImageNotFound(name)

    def rebuild(self, nocache: bool = False):
        if self.dockerfile is None:
            _ = self.pull()
            return
        elif not self.dockerfile.exists():
            raise ValueError("Could not find the Dockerfile.")
        # use the low-level APIClient so we can get streaming build status
        try:
            sock = _discover_podman_socket()
            cli = docker.APIClient(base_url=sock)
        except DockerException as e:
            raise ValueError(f"Could not connect to socket: sock={sock} {e}") from e
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
                        line = json.loads(line)
                    except json.decoder.JSONDecodeError:
                        continue
                    if "stream" in line:
                        m = re.match(
                            r"^Step\s+(\d+)(/(\d+))?\s+:\s+(.+)$",
                            line["stream"],
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
                                new_line = self.dockerfile.get_line(
                                    m.group(4), starting_line=last_line
                                )
                                if new_line is not None:
                                    t.update(new_line - last_line)
                                    last_line = new_line
                        t.write(line["stream"].replace("\n", "").strip())
