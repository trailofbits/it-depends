"""Unit tests for ``it_depends.docker``.

Everything here is pure or mocked: no Docker daemon, no network, no containers.
Private helpers are exercised directly where they are the unit under test.
"""

import json
import subprocess
from pathlib import Path
from sys import version_info
from unittest import mock

if version_info < (3, 11):
    from typing_extensions import Self
else:
    from typing import Self

import pytest
from docker.errors import DockerException
from docker.errors import NotFound as ImageNotFound

from it_depends import __version__ as it_depends_version
from it_depends import docker as docker_mod
from it_depends.docker import (
    DockerContainer,
    Dockerfile,
    InMemoryDockerfile,
    InMemoryFile,
    _discover_podman_socket,
)

_DOCKER_BINARY = "/usr/bin/docker"
_IMAGE_TAG = "1.0"
_TWO_STEPS = 2
_THREE_LINES = 3
_FROM_LINE = "FROM base\n"
_RUN_LINE = "RUN x\n"


class _FakeOS:
    """Minimal stand-in for the ``os`` module used by ``_discover_podman_socket``."""

    def __init__(self, euid: int, environ: dict[str, str]) -> None:
        self._euid = euid
        self.environ = environ

    def geteuid(self) -> int:
        return self._euid


class _FakePath:
    """Minimal stand-in for ``pathlib.Path`` used by ``_discover_podman_socket``.

    The probe only uses ``__truediv__``, ``is_dir``, ``is_socket`` and ``str``.
    """

    dir_exists = True
    socket_exists = True

    def __init__(self, value: object) -> None:
        self.value = str(value)

    def __truediv__(self, other: object) -> "_FakePath":
        return _FakePath(f"{self.value.rstrip('/')}/{other}")

    def is_dir(self) -> bool:
        return type(self).dir_exists

    def is_socket(self) -> bool:
        return type(self).socket_exists

    def __str__(self) -> str:
        return self.value


class _RecordingProgress:
    """Stand-in for the ``tqdm`` progress bar used by ``DockerContainer.rebuild``."""

    def __init__(self) -> None:
        self.desc = ""
        self.total = 0
        self.updates: list[int] = []
        self.writes: list[str] = []

    def __enter__(self) -> Self:
        return self

    def __exit__(self, exc_type: object, exc_val: object, exc_tb: object) -> None:
        return None

    def update(self, steps: int) -> None:
        self.updates.append(steps)

    def write(self, text: str) -> None:
        self.writes.append(text)


def _write_dockerfile(tmp_path: Path, content: str) -> Dockerfile:
    path = tmp_path / "Dockerfile"
    path.write_text(content)
    return Dockerfile(path)


def _container(*, dockerfile: Dockerfile | None = None, tag: str | None = _IMAGE_TAG) -> DockerContainer:
    container = DockerContainer("img", dockerfile=dockerfile, tag=tag)
    # Skip ``resolve_executable`` in unit tests; the property is tested separately.
    container._docker_path = _DOCKER_BINARY  # noqa: SLF001
    return container


def _mock_client(*images: mock.Mock) -> mock.Mock:
    client = mock.Mock()
    client.images.list.return_value = list(images)
    return client


# --------------------------------------------------------------------------- #
# _discover_podman_socket
# --------------------------------------------------------------------------- #


def test_discover_podman_socket_rootless_uses_xdg_runtime_dir(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(docker_mod, "os", _FakeOS(1000, {"XDG_RUNTIME_DIR": "/run/user/1000"}))
    monkeypatch.setattr(docker_mod, "Path", _FakePath)
    monkeypatch.setattr(_FakePath, "dir_exists", True)
    monkeypatch.setattr(_FakePath, "socket_exists", True)

    assert _discover_podman_socket() == "unix:///run/user/1000/podman/podman.sock"


def test_discover_podman_socket_rootless_falls_back_to_default_runtime_dir(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(docker_mod, "os", _FakeOS(1000, {}))
    monkeypatch.setattr(docker_mod, "Path", _FakePath)
    monkeypatch.setattr(_FakePath, "dir_exists", True)
    monkeypatch.setattr(_FakePath, "socket_exists", True)

    assert _discover_podman_socket() == "unix:///run/user/1000/podman/podman.sock"


def test_discover_podman_socket_returns_none_without_a_runtime_dir(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(docker_mod, "os", _FakeOS(1000, {"XDG_RUNTIME_DIR": "/run/user/1000"}))
    monkeypatch.setattr(docker_mod, "Path", _FakePath)
    monkeypatch.setattr(_FakePath, "dir_exists", False)
    monkeypatch.setattr(_FakePath, "socket_exists", True)

    assert _discover_podman_socket() is None


def test_discover_podman_socket_returns_none_when_the_socket_is_not_a_socket(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(docker_mod, "os", _FakeOS(1000, {"XDG_RUNTIME_DIR": "/run/user/1000"}))
    monkeypatch.setattr(docker_mod, "Path", _FakePath)
    monkeypatch.setattr(_FakePath, "dir_exists", True)
    monkeypatch.setattr(_FakePath, "socket_exists", False)

    assert _discover_podman_socket() is None


def test_discover_podman_socket_root_uses_the_system_socket(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(docker_mod, "os", _FakeOS(0, {}))
    monkeypatch.setattr(docker_mod, "Path", _FakePath)
    monkeypatch.setattr(_FakePath, "socket_exists", True)

    assert _discover_podman_socket() == "unix:///run/podman/podman.sock"


def test_discover_podman_socket_root_returns_none_when_the_system_socket_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(docker_mod, "os", _FakeOS(0, {}))
    monkeypatch.setattr(docker_mod, "Path", _FakePath)
    monkeypatch.setattr(_FakePath, "socket_exists", False)

    assert _discover_podman_socket() is None


# --------------------------------------------------------------------------- #
# Dockerfile
# --------------------------------------------------------------------------- #


def test_dockerfile_path_exists_and_dir(tmp_path: Path) -> None:
    dockerfile = _write_dockerfile(tmp_path, _FROM_LINE)

    assert dockerfile.path == tmp_path / "Dockerfile"
    assert dockerfile.exists() is True
    assert dockerfile.dir() == tmp_path


def test_dockerfile_exists_is_false_for_a_missing_file(tmp_path: Path) -> None:
    assert Dockerfile(tmp_path / "missing").exists() is False


def test_dockerfile_context_manager_returns_itself(tmp_path: Path) -> None:
    dockerfile = _write_dockerfile(tmp_path, _FROM_LINE)

    with dockerfile as entered:
        assert entered is dockerfile


def test_dockerfile_len_counts_newlines(tmp_path: Path) -> None:
    dockerfile = _write_dockerfile(tmp_path, _FROM_LINE + _RUN_LINE)

    assert len(dockerfile) == _TWO_STEPS


def test_dockerfile_len_ignores_a_file_without_a_trailing_newline(tmp_path: Path) -> None:
    dockerfile = _write_dockerfile(tmp_path, "FROM base")

    assert len(dockerfile) == 0


def test_dockerfile_len_is_cached_until_the_path_changes(tmp_path: Path) -> None:
    dockerfile = _write_dockerfile(tmp_path, _FROM_LINE)
    assert len(dockerfile) == 1

    replacement = tmp_path / "Dockerfile.replacement"
    replacement.write_text(_FROM_LINE + _RUN_LINE + "FROM other\n")
    dockerfile.path = replacement

    assert len(dockerfile) == _THREE_LINES


def test_dockerfile_get_line_finds_the_step_command(tmp_path: Path) -> None:
    dockerfile = _write_dockerfile(tmp_path, _FROM_LINE + _RUN_LINE + "FROM other\n")

    assert dockerfile.get_line(_FROM_LINE) == 0
    assert dockerfile.get_line(_RUN_LINE, starting_line=1) == 1
    assert dockerfile.get_line("FROM other\n", starting_line=2) == _TWO_STEPS


def test_dockerfile_get_line_returns_none_for_an_unknown_command(tmp_path: Path) -> None:
    dockerfile = _write_dockerfile(tmp_path, _FROM_LINE + _RUN_LINE)

    assert dockerfile.get_line("RUN unknown\n") is None


def test_dockerfile_get_line_returns_none_for_an_out_of_range_starting_line(tmp_path: Path) -> None:
    dockerfile = _write_dockerfile(tmp_path, _FROM_LINE)

    assert dockerfile.get_line(_FROM_LINE, starting_line=99) is None


# --------------------------------------------------------------------------- #
# InMemoryFile / InMemoryDockerfile
# --------------------------------------------------------------------------- #


def test_in_memory_file_stores_its_fields() -> None:
    in_memory = InMemoryFile("script.sh", b"#!/bin/sh\n")

    assert in_memory.filename == "script.sh"
    assert in_memory.content == b"#!/bin/sh\n"


def test_in_memory_dockerfile_path_before_entering_is_a_dummy_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)

    dockerfile = InMemoryDockerfile(_FROM_LINE)

    assert dockerfile.path == tmp_path / "dummy"


def test_in_memory_dockerfile_writes_content_and_local_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    dockerfile = InMemoryDockerfile(_FROM_LINE, [InMemoryFile("script.sh", b"#!/bin/sh\n")])

    with dockerfile:
        path = dockerfile.path
        tmpdir = path.parent
        assert path.read_text() == _FROM_LINE
        assert (tmpdir / "script.sh").read_bytes() == b"#!/bin/sh\n"

    assert not path.exists()
    assert not tmpdir.exists()
    with pytest.raises(ValueError, match="only has a valid path when inside of its context manager"):
        _ = dockerfile.path


def test_in_memory_dockerfile_nested_entries_keep_the_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    dockerfile = InMemoryDockerfile(_FROM_LINE)

    with dockerfile:
        path = dockerfile.path
        with dockerfile:
            assert dockerfile.path == path
            assert path.exists()
        assert path.exists()

    assert not path.exists()


# --------------------------------------------------------------------------- #
# DockerContainer: construction and cached attributes
# --------------------------------------------------------------------------- #


def test_container_defaults_to_the_package_version() -> None:
    container = DockerContainer("img")

    assert container.image_name == "img"
    assert container.tag == it_depends_version
    assert container.dockerfile is None
    assert container.name == f"img:{it_depends_version}"


def test_container_uses_an_explicit_tag() -> None:
    container = DockerContainer("img", tag=_IMAGE_TAG)

    assert container.name == f"img:{_IMAGE_TAG}"


def test_container_docker_path_is_resolved_once(monkeypatch: pytest.MonkeyPatch) -> None:
    resolve = mock.Mock(return_value=_DOCKER_BINARY)
    monkeypatch.setattr(docker_mod, "resolve_executable", resolve)
    container = DockerContainer("img")

    assert container.docker_path == _DOCKER_BINARY
    assert container.docker_path == _DOCKER_BINARY
    resolve.assert_called_once_with("docker")


def test_container_client_is_created_once(monkeypatch: pytest.MonkeyPatch) -> None:
    client = mock.Mock()
    from_env = mock.Mock(return_value=client)
    monkeypatch.setattr(docker_mod.docker, "from_env", from_env)
    container = DockerContainer("img")

    assert container.client is client
    assert container.client is client
    from_env.assert_called_once_with()


def test_exists_returns_the_image_with_the_matching_tag() -> None:
    image = mock.Mock(tags=["other:2.0", f"img:{_IMAGE_TAG}"])
    container = _container()
    container._client = _mock_client(image)  # noqa: SLF001

    assert container.exists() is image


def test_exists_returns_none_when_no_image_matches() -> None:
    container = _container()
    container._client = _mock_client(mock.Mock(tags=["other:2.0"]))  # noqa: SLF001

    assert container.exists() is None


# --------------------------------------------------------------------------- #
# DockerContainer.pull
# --------------------------------------------------------------------------- #


def test_pull_returns_the_pulled_image(monkeypatch: pytest.MonkeyPatch) -> None:
    image = mock.Mock(tags=[f"img:{_IMAGE_TAG}"])
    check_call = mock.Mock()
    monkeypatch.setattr(docker_mod.subprocess, "check_call", check_call)
    container = _container()
    container._client = _mock_client(image)  # noqa: SLF001

    assert container.pull() is image
    check_call.assert_called_once_with([_DOCKER_BINARY, "pull", f"img:{_IMAGE_TAG}"])


def test_pull_latest_uses_the_latest_tag(monkeypatch: pytest.MonkeyPatch) -> None:
    image = mock.Mock(tags=["img:latest"])
    check_call = mock.Mock()
    monkeypatch.setattr(docker_mod.subprocess, "check_call", check_call)
    container = _container()
    container._client = _mock_client(image)  # noqa: SLF001

    assert container.pull(latest=True) is image
    check_call.assert_called_once_with([_DOCKER_BINARY, "pull", "img:latest"])


def test_pull_raises_when_the_image_is_missing_after_pulling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(docker_mod.subprocess, "check_call", mock.Mock())
    container = _container()
    container._client = _mock_client(mock.Mock(tags=["other:2.0"]))  # noqa: SLF001

    with pytest.raises(ImageNotFound):
        container.pull()


def test_pull_warns_and_raises_when_the_docker_command_fails(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(
        docker_mod.subprocess,
        "check_call",
        mock.Mock(side_effect=subprocess.CalledProcessError(1, "docker")),
    )
    container = _container()
    container._client = _mock_client()  # noqa: SLF001

    with caplog.at_level("WARNING", logger="it_depends.docker"), pytest.raises(ImageNotFound):
        container.pull()

    assert "Failed to pull Docker image" in caplog.text


# --------------------------------------------------------------------------- #
# DockerContainer.run
# --------------------------------------------------------------------------- #


def test_run_interactive_builds_the_expected_command(monkeypatch: pytest.MonkeyPatch) -> None:
    container = _container()
    rebuild = mock.Mock()
    monkeypatch.setattr(container, "rebuild", rebuild)
    call = mock.Mock(return_value=0)
    monkeypatch.setattr(docker_mod.subprocess, "call", call)

    result = container.run("echo", "hi")

    expected = [_DOCKER_BINARY, "run", "-it", "--rm", f"img:{_IMAGE_TAG}", "echo", "hi"]
    rebuild.assert_called_once_with()
    assert call.call_args.args[0] == expected
    assert call.call_args.kwargs["cwd"] == str(Path.cwd())
    assert isinstance(result, subprocess.CompletedProcess)
    assert result.returncode == 0


def test_run_non_interactive_passes_mounts_env_privileged_and_stdio(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    container = _container()
    monkeypatch.setattr(container, "rebuild", mock.Mock())
    run = mock.Mock(return_value="sentinel")
    monkeypatch.setattr(docker_mod.subprocess, "run", run)
    mount_source = tmp_path / "source"

    result = container.run(
        "ls",
        interactive=False,
        mounts=[(mount_source, "/mnt"), (tmp_path / "rel", Path("/abs"))],
        env={"A": 'va"lue'},
        privileged=True,
        stdin=1,
        stdout=2,
        stderr=3,
        cwd="/work",
    )

    expected = [
        _DOCKER_BINARY,
        "run",
        "--rm",
        "-v",
        f"{mount_source.absolute()}:/mnt:cached",
        "-v",
        f"{(tmp_path / 'rel').absolute()}:/abs:cached",
        "-e",
        'A=va\\"lue',
        "--privileged=true",
        f"img:{_IMAGE_TAG}",
        "ls",
    ]
    assert result == "sentinel"
    run.assert_called_once_with(expected, check=False, stdin=1, stdout=2, stderr=3, cwd="/work")


def test_run_rejects_stdio_options_with_interactive(monkeypatch: pytest.MonkeyPatch) -> None:
    container = _container()
    monkeypatch.setattr(container, "rebuild", mock.Mock())

    with pytest.raises(ValueError, match="all of `stdin`, `stdout`, and `stderr` must be `None`"):
        container.run("x", stdout=1)


def test_run_requires_the_image_without_build_if_necessary(monkeypatch: pytest.MonkeyPatch) -> None:
    container = _container()
    monkeypatch.setattr(container, "exists", mock.Mock(return_value=None))

    with pytest.raises(ValueError, match="Re-run with `build_if_necessary=True`"):
        container.run("x", rebuild=False, build_if_necessary=False)


def test_run_pulls_when_no_dockerfile_left_the_image_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    container = _container()
    monkeypatch.setattr(container, "exists", mock.Mock(return_value=None))
    pull = mock.Mock()
    monkeypatch.setattr(container, "pull", pull)

    with pytest.raises(ValueError, match="does not exist!"):
        container.run("x", rebuild=False)

    pull.assert_called_once_with()


def test_run_rebuilds_the_dockerfile_then_raises_when_still_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    dockerfile = mock.Mock(spec=Dockerfile)
    dockerfile.exists.return_value = True
    container = _container(dockerfile=dockerfile)
    exists = mock.Mock(return_value=None)
    monkeypatch.setattr(container, "exists", exists)
    rebuild = mock.Mock()
    monkeypatch.setattr(container, "rebuild", rebuild)

    with pytest.raises(ValueError, match="does not exist"):
        container.run("x", rebuild=False)

    rebuild.assert_called_once_with()
    assert exists.call_count == _TWO_STEPS


def test_run_skips_the_build_when_the_image_exists(monkeypatch: pytest.MonkeyPatch) -> None:
    container = _container()
    monkeypatch.setattr(container, "exists", mock.Mock(return_value=mock.Mock()))
    rebuild = mock.Mock()
    pull = mock.Mock()
    monkeypatch.setattr(container, "rebuild", rebuild)
    monkeypatch.setattr(container, "pull", pull)
    run = mock.Mock(return_value="done")
    monkeypatch.setattr(docker_mod.subprocess, "run", run)

    assert container.run("true", rebuild=False, interactive=False) == "done"

    rebuild.assert_not_called()
    pull.assert_not_called()
    assert run.call_args.kwargs["cwd"] == str(Path.cwd())


# --------------------------------------------------------------------------- #
# DockerContainer.rebuild
# --------------------------------------------------------------------------- #


def test_rebuild_pulls_when_no_dockerfile_is_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    container = _container()
    pull = mock.Mock()
    monkeypatch.setattr(container, "pull", pull)

    container.rebuild()

    pull.assert_called_once_with()


def test_rebuild_raises_when_the_dockerfile_does_not_exist() -> None:
    dockerfile = mock.Mock(spec=Dockerfile)
    dockerfile.exists.return_value = False
    container = _container(dockerfile=dockerfile)

    with pytest.raises(ValueError, match=r"Could not find the Dockerfile\."):
        container.rebuild()


def test_rebuild_wraps_a_socket_connection_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    dockerfile = _write_dockerfile(tmp_path, _FROM_LINE)
    container = _container(dockerfile=dockerfile)
    monkeypatch.setattr(docker_mod, "_discover_podman_socket", lambda: "unix:///sock")
    monkeypatch.setattr(docker_mod.docker, "APIClient", mock.Mock(side_effect=DockerException("boom")))

    with pytest.raises(ValueError, match="Could not connect to socket"):
        container.rebuild()


def test_rebuild_parses_step_totals_from_the_stream(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    dockerfile = _write_dockerfile(tmp_path, _FROM_LINE + _RUN_LINE)
    container = _container(dockerfile=dockerfile)
    api = mock.Mock()
    api.build.return_value = iter(
        [
            json.dumps({"stream": "Step 1/2 : FROM base\n"}).encode(),
            b"not json",
            json.dumps({"not_stream": 1}).encode(),
            (json.dumps({"stream": "Step 2/2 : RUN x\n"}) + "\n" + json.dumps({"stream": ""})).encode(),
        ]
    )
    monkeypatch.setattr(docker_mod, "_discover_podman_socket", lambda: "unix:///sock")
    monkeypatch.setattr(docker_mod.docker, "APIClient", mock.Mock(return_value=api))
    progress = _RecordingProgress()
    monkeypatch.setattr(docker_mod, "tqdm", lambda **_kwargs: progress)

    container.rebuild()

    api.build.assert_called_once_with(
        path=str(tmp_path),
        rm=True,
        tag=f"img:{_IMAGE_TAG}",
        nocache=False,
        forcerm=True,
    )
    assert progress.total == _TWO_STEPS
    assert progress.updates == [1, 1]
    assert progress.writes == ["Step 1/2 : FROM base", "Step 2/2 : RUN x", ""]


def test_rebuild_infers_the_step_count_from_the_dockerfile(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    dockerfile = _write_dockerfile(tmp_path, _FROM_LINE + _RUN_LINE + "FROM other\n")
    container = _container(dockerfile=dockerfile)
    api = mock.Mock()
    api.build.return_value = iter([json.dumps({"stream": "Step 1 : FROM base\n"}).encode()])
    monkeypatch.setattr(docker_mod, "_discover_podman_socket", lambda: None)
    monkeypatch.setattr(docker_mod.docker, "APIClient", mock.Mock(return_value=api))
    progress = _RecordingProgress()
    monkeypatch.setattr(docker_mod, "tqdm", lambda **_kwargs: progress)

    container.rebuild(nocache=True)

    assert progress.total == _THREE_LINES
    assert progress.updates == []
    assert progress.writes == ["Step 1 : FROM base"]
    assert api.build.call_args.kwargs["nocache"] is True


def test_rebuild_infers_the_step_line_when_the_dockerfile_has_no_trailing_newline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # ``get_line`` compares whole lines, including the newline, so a command can only
    # match when it is the final line of a file that has no trailing newline.
    dockerfile = _write_dockerfile(tmp_path, _FROM_LINE + _RUN_LINE + "FROM other")
    container = _container(dockerfile=dockerfile)
    api = mock.Mock()
    api.build.return_value = iter([json.dumps({"stream": "Step 3 : FROM other\n"}).encode()])
    monkeypatch.setattr(docker_mod, "_discover_podman_socket", lambda: None)
    monkeypatch.setattr(docker_mod.docker, "APIClient", mock.Mock(return_value=api))
    progress = _RecordingProgress()
    monkeypatch.setattr(docker_mod, "tqdm", lambda **_kwargs: progress)

    container.rebuild()

    assert progress.total == _TWO_STEPS
    assert progress.updates == [_TWO_STEPS]
    assert progress.writes == ["Step 3 : FROM other"]


def test_rebuild_passes_none_as_the_socket_when_discovery_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dockerfile = _write_dockerfile(tmp_path, _FROM_LINE)
    container = _container(dockerfile=dockerfile)
    api = mock.Mock()
    api.build.return_value = iter([])
    api_client = mock.Mock(return_value=api)
    monkeypatch.setattr(docker_mod, "_discover_podman_socket", lambda: None)
    monkeypatch.setattr(docker_mod.docker, "APIClient", api_client)
    progress = _RecordingProgress()
    monkeypatch.setattr(docker_mod, "tqdm", lambda **_kwargs: progress)

    container.rebuild()

    api_client.assert_called_once_with(base_url=None)
