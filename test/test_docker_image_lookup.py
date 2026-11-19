"""Image existence checks avoid enumerating unrelated Docker images."""

import errno
from unittest.mock import Mock

import docker
import pytest
from docker.errors import APIError, DockerException, ImageNotFound, NotFound
from docker.models.images import Image
from requests.exceptions import ConnectionError as RequestsConnectionError

from it_depends.docker import DockerContainer


@pytest.fixture
def image_client(monkeypatch: pytest.MonkeyPatch) -> Mock:
    client = Mock(spec=docker.DockerClient)
    client.images.list.side_effect = AssertionError("Existence checks must not enumerate images")
    monkeypatch.setattr("it_depends.docker.docker.from_env", lambda: client)
    return client


def test_exists_returns_the_requested_image(image_client: Mock) -> None:
    image = Image(attrs={"Id": "sha256:example"})
    image_client.images.get.return_value = image
    container = DockerContainer("example/image", tag="lookup-test")
    assert container.exists() is image
    image_client.images.get.assert_called_once_with("example/image:lookup-test")


@pytest.mark.parametrize(
    "error",
    [ImageNotFound("Image does not exist"), NotFound("Image does not exist")],
    ids=["image-not-found", "generic-not-found"],
)
def test_exists_returns_none_for_a_missing_image(image_client: Mock, error: NotFound) -> None:
    image_client.images.get.side_effect = error
    container = DockerContainer("example/image", tag="lookup-test")
    assert container.exists() is None
    image_client.images.get.assert_called_once_with("example/image:lookup-test")


@pytest.mark.parametrize(
    "error",
    [
        pytest.param(DockerException("Daemon unavailable"), id="daemon-failure"),
        pytest.param(APIError("Permission denied"), id="api-failure"),
        pytest.param(
            RequestsConnectionError(OSError(errno.EMFILE, "Too many open files")),
            id="descriptor-exhaustion",
        ),
    ],
)
def test_exists_propagates_other_failures(image_client: Mock, error: Exception) -> None:
    image_client.images.get.side_effect = error
    container = DockerContainer("example/image", tag="lookup-test")
    with pytest.raises(type(error)) as raised:
        container.exists()
    assert raised.value is error
