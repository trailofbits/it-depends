"""Tests for the Ubuntu os-release detection in ubuntu/docker.py."""

import io
from unittest import TestCase
from unittest.mock import patch

from it_depends.ubuntu.docker import is_running_ubuntu


class _FakePath:
    """Stands in for Path("/etc/os-release") with controllable contents."""

    def __init__(self, contents: str | None) -> None:
        self._contents = contents

    def exists(self) -> bool:
        return self._contents is not None

    def open(self, *args: object, **kwargs: object) -> io.StringIO:
        assert self._contents is not None
        return io.StringIO(self._contents)


OS_RELEASE_UBUNTU_22 = 'NAME="Ubuntu"\nVERSION_ID="22.04"\n'
OS_RELEASE_UBUNTU_NO_VERSION = 'NAME="Ubuntu"\n'
OS_RELEASE_DEBIAN = 'NAME="Debian GNU/Linux"\nVERSION_ID="12"\n'


class TestIsRunningUbuntu(TestCase):
    """Tests for is_running_ubuntu."""

    def setUp(self) -> None:
        # The function is lru_cached, so each case has to start clean.
        is_running_ubuntu.cache_clear()

    def tearDown(self) -> None:
        is_running_ubuntu.cache_clear()

    def _call(self, contents: str | None, check_version: str | None = None) -> bool:
        with patch("it_depends.ubuntu.docker.Path", return_value=_FakePath(contents)):
            return is_running_ubuntu(check_version)

    def test_false_when_os_release_is_absent(self) -> None:
        assert self._call(None) is False

    def test_true_on_ubuntu_without_a_version_check(self) -> None:
        assert self._call(OS_RELEASE_UBUNTU_22) is True

    def test_true_when_the_requested_version_matches(self) -> None:
        assert self._call(OS_RELEASE_UBUNTU_22, "22.04") is True

    def test_false_when_the_requested_version_differs(self) -> None:
        assert self._call(OS_RELEASE_UBUNTU_22, "24.04") is False

    def test_true_on_ubuntu_without_a_version_id_when_none_requested(self) -> None:
        assert self._call(OS_RELEASE_UBUNTU_NO_VERSION) is True

    def test_false_when_the_requested_version_is_absent_from_the_file(self) -> None:
        assert self._call(OS_RELEASE_UBUNTU_NO_VERSION, "22.04") is False

    def test_false_on_a_non_ubuntu_system(self) -> None:
        assert self._call(OS_RELEASE_DEBIAN) is False

    def test_false_on_a_non_ubuntu_system_with_a_version_check(self) -> None:
        assert self._call(OS_RELEASE_DEBIAN, "12") is False

    def test_the_name_match_is_case_insensitive_and_tolerates_whitespace(self) -> None:
        assert self._call('  name = "ubuntu"  \n') is True

    def test_stops_reading_once_the_version_is_known(self) -> None:
        # Once version_id has been captured the loop breaks on the next line
        # rather than scanning the rest of the file.
        contents = 'NAME="Ubuntu"\nVERSION_ID="22.04"\nPRETTY_NAME="Ubuntu 22.04.3 LTS"\n'
        assert self._call(contents, "22.04") is True
