"""Tests for the CMake resolver's argument parsing and availability checks."""

from unittest import TestCase
from unittest.mock import patch

from it_depends.cmake import CMakeResolver


class TestGetNames(TestCase):
    """Tests for CMakeResolver._get_names, which is pure."""

    def setUp(self) -> None:
        self.resolver = CMakeResolver()

    def test_returns_nothing_without_a_names_keyword(self) -> None:
        assert self.resolver._get_names(["FIND_LIBRARY", "foo"], ["REQUIRED"]) == []

    def test_returns_everything_after_names_when_no_keyword_matches(self) -> None:
        assert self.resolver._get_names(["FIND_LIBRARY", "NAMES", "a", "b"], []) == ["a", "b"]

    def test_returns_nothing_when_names_is_last(self) -> None:
        assert self.resolver._get_names(["FIND_LIBRARY", "NAMES"], []) == []

    def test_stops_at_the_first_matching_keyword(self) -> None:
        args = ["FIND_LIBRARY", "NAMES", "a", "b", "REQUIRED", "c"]
        assert self.resolver._get_names(args, ["REQUIRED"]) == ["a", "b"]

    def test_stops_on_a_keyword_prefix(self) -> None:
        # startswith is what the implementation tests, not equality.
        args = ["NAMES", "a", "PATHS", "/usr"]
        assert self.resolver._get_names(args, ["PATH"]) == ["a"]

    def test_splits_semicolon_separated_names(self) -> None:
        assert self.resolver._get_names(["NAMES", "a;b;c"], []) == ["a", "b", "c"]

    def test_uses_the_first_names_keyword(self) -> None:
        args = ["NAMES", "a", "NAMES", "b"]
        assert self.resolver._get_names(args, []) == ["a", "NAMES", "b"]


class TestIsAvailable(TestCase):
    """Tests for CMakeResolver.is_available."""

    def setUp(self) -> None:
        # CMakeResolver() returns a shared instance, so a tool path cached by an
        # earlier test would otherwise short-circuit the resolution below.
        CMakeResolver()._tool_path = None

    def test_unavailable_when_parse_cmake_is_missing(self) -> None:
        with patch("it_depends.cmake.cmake_parsing", None):
            availability = CMakeResolver().is_available()
        assert availability.is_available is False
        assert "parse_cmake" in (availability.reason or "")

    def test_unavailable_when_the_cmake_binary_is_missing(self) -> None:
        with patch("it_depends.cmake.cmake_parsing", object()):
            with patch("it_depends.cmake.resolve_executable", side_effect=FileNotFoundError):
                availability = CMakeResolver().is_available()
        assert availability.is_available is False
        assert "cmake" in (availability.reason or "")

    def test_available_when_both_are_present(self) -> None:
        with patch("it_depends.cmake.cmake_parsing", object()):
            with patch("it_depends.cmake.resolve_executable", return_value="/usr/bin/cmake"):
                availability = CMakeResolver().is_available()
        assert availability.is_available is True


class TestToolPath(TestCase):
    """Tests for CMakeResolver.tool_path caching."""

    def setUp(self) -> None:
        # See TestIsAvailable.setUp.
        CMakeResolver()._tool_path = None

    def test_resolves_once_and_caches(self) -> None:
        resolver = CMakeResolver()
        with patch("it_depends.cmake.resolve_executable", return_value="/usr/bin/cmake") as mock:
            assert resolver.tool_path == "/usr/bin/cmake"
            assert resolver.tool_path == "/usr/bin/cmake"
        assert mock.call_count == 1

    def test_the_resolver_is_shared_so_the_path_is_cached_across_constructions(self) -> None:
        # CMakeResolver() hands back the same instance, so a second construction
        # does not re-resolve. Worth pinning: it is why a cached tool path leaks
        # between tests unless setUp clears it (see above).
        with patch("it_depends.cmake.resolve_executable", return_value="/usr/bin/cmake") as mock:
            first = CMakeResolver()
            second = CMakeResolver()
            assert first is second
            first.tool_path
            second.tool_path
        assert mock.call_count == 1
