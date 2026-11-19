"""Docker build endpoint selection with network requests mocked."""

import hashlib
import json
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import Mock, patch

import docker
import pytest
from docker.errors import DockerException

from it_depends.docker import DockerContainer, Dockerfile


@pytest.fixture
def build_setup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[tuple[DockerContainer, Mock]]:
    for name in ("DOCKER_HOST", "DOCKER_TLS_VERIFY", "DOCKER_CERT_PATH"):
        monkeypatch.delenv(name, raising=False)
    config_dir = tmp_path / "docker-config"
    config_dir.mkdir()
    (config_dir / "config.json").write_text("{}")
    monkeypatch.setenv("DOCKER_CONFIG", str(config_dir))
    monkeypatch.setenv("DOCKER_CONTEXT", "default")
    monkeypatch.setattr("it_depends.docker._discover_podman_socket", lambda: None)
    path = tmp_path / "Dockerfile"
    path.write_text("FROM scratch\n")
    container = DockerContainer("example/image", Dockerfile(path), tag="connection-test")
    with (
        patch.object(docker.APIClient, "_retrieve_server_version", return_value="1.45"),
        patch.object(docker.APIClient, "build", autospec=True, return_value=[]) as build,
    ):
        yield container, build
        if build.call_args is not None:
            build.call_args.args[0].close()


@pytest.mark.parametrize(
    "host",
    [
        None,
        "unix:///custom/docker.sock",
        "unix:///run/user/1000/docker.sock",
        "tcp://docker.example.test:2375",
    ],
    ids=["default-docker", "custom-socket", "rootless-docker", "remote-docker"],
)
def test_build_uses_configured_endpoint(
    build_setup: tuple[DockerContainer, Mock],
    monkeypatch: pytest.MonkeyPatch,
    host: str | None,
) -> None:
    container, build = build_setup
    if host is not None:
        monkeypatch.setenv("DOCKER_HOST", host)
        monkeypatch.setenv("DOCKER_CONTEXT", "unused-context")
    container.rebuild()
    api = build.call_args.args[0]
    if host is None or host.startswith("unix://"):
        expected = "/var/run/docker.sock" if host is None else host.removeprefix("unix://")
        assert api.get_adapter(api.base_url).socket_path == expected
    else:
        assert api.base_url == "http://docker.example.test:2375"


def test_build_preserves_tls_configuration(
    build_setup: tuple[DockerContainer, Mock],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    container, build = build_setup
    for name in ("ca.pem", "cert.pem", "key.pem"):
        (tmp_path / name).write_text("unused by mocked network requests\n")
    monkeypatch.setenv("DOCKER_HOST", "tcp://docker.example.test:2376")
    monkeypatch.setenv("DOCKER_TLS_VERIFY", "1")
    monkeypatch.setenv("DOCKER_CERT_PATH", str(tmp_path))
    container.rebuild()
    api = build.call_args.args[0]
    assert api.base_url == "https://docker.example.test:2376"
    assert api.verify == str(tmp_path / "ca.pem")
    assert api.cert == (str(tmp_path / "cert.pem"), str(tmp_path / "key.pem"))


@pytest.mark.skipif(
    not hasattr(docker.DockerClient, "from_context"),
    reason="Docker CLI contexts require Docker SDK 7.2.0 or later",
)
@pytest.mark.parametrize("select_with_env", [False, True], ids=["active-context", "DOCKER_CONTEXT"])
def test_build_uses_docker_context(
    build_setup: tuple[DockerContainer, Mock],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    select_with_env: bool,
) -> None:
    container, build = build_setup
    context_name = "test-daemon"
    config_dir = tmp_path / "docker-config"
    digest = hashlib.sha256(context_name.encode()).hexdigest()
    metadata_dir = config_dir / "contexts" / "meta" / digest
    metadata_dir.mkdir(parents=True)
    (metadata_dir / "meta.json").write_text(
        json.dumps(
            {
                "Name": context_name,
                "Metadata": {},
                "Endpoints": {"docker": {"Host": "unix:///context/docker.sock", "SkipTLSVerify": True}},
            },
        ),
    )
    selected = "default" if select_with_env else context_name
    (config_dir / "config.json").write_text(json.dumps({"currentContext": selected}))
    if select_with_env:
        monkeypatch.setenv("DOCKER_CONTEXT", context_name)
    else:
        monkeypatch.delenv("DOCKER_CONTEXT")
    container.rebuild()
    api = build.call_args.args[0]
    assert api.get_adapter(api.base_url).socket_path == "/context/docker.sock"


@pytest.mark.parametrize(
    "socket",
    ["unix:///run/user/1000/podman/podman.sock", "unix:///run/podman/podman.sock"],
    ids=["rootless-podman", "rootful-podman"],
)
def test_build_preserves_discovered_podman_socket(
    build_setup: tuple[DockerContainer, Mock],
    monkeypatch: pytest.MonkeyPatch,
    socket: str,
) -> None:
    container, build = build_setup
    monkeypatch.setenv("DOCKER_HOST", "tcp://docker.example.test:2375")
    monkeypatch.setattr("it_depends.docker._discover_podman_socket", lambda: socket)
    container.rebuild()
    api = build.call_args.args[0]
    assert api.get_adapter(api.base_url).socket_path == socket.removeprefix("unix://")


@pytest.mark.parametrize("socket", [None, "unix:///run/podman/podman.sock"])
def test_build_connection_failure_has_context(
    build_setup: tuple[DockerContainer, Mock],
    monkeypatch: pytest.MonkeyPatch,
    socket: str | None,
) -> None:
    container, _build = build_setup
    monkeypatch.setattr("it_depends.docker._discover_podman_socket", lambda: socket)
    with (
        patch.object(
            docker.APIClient,
            "_retrieve_server_version",
            side_effect=DockerException("Daemon unavailable"),
        ),
        pytest.raises(
            ValueError,
            match="Could not connect to container runtime: Daemon unavailable",
        ) as raised,
    ):
        container.rebuild()
    assert isinstance(raised.value.__cause__, DockerException)
