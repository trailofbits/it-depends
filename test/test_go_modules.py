"""Tests for the Go version, go.mod and meta-import helpers."""

from unittest import TestCase

import pytest

from it_depends.go import (
    GoModule,
    GoSpec,
    GoVersion,
    MetaImport,
)
from it_depends.vcs import VCSResolutionError


class TestGoVersion(TestCase):
    """Tests for GoVersion."""

    def test_strips_surrounding_whitespace(self) -> None:
        assert GoVersion("  v1.2.3  ").version_string == "v1.2.3"

    def test_removes_a_leading_equals(self) -> None:
        assert GoVersion("=v1.2.3").version_string == "v1.2.3"

    def test_build_flag_is_false_for_simple_spec_compatibility(self) -> None:
        assert GoVersion("v1.2.3").build is False

    def test_str_returns_the_version_string(self) -> None:
        assert str(GoVersion("v1.2.3")) == "v1.2.3"

    def test_equality_is_by_version_string(self) -> None:
        assert GoVersion("v1.2.3") == GoVersion("v1.2.3")
        assert GoVersion("v1.2.3") != GoVersion("v1.2.4")

    def test_equality_with_a_non_go_version_is_false(self) -> None:
        assert GoVersion("v1.2.3").__eq__("v1.2.3") is False

    def test_hash_matches_for_equal_versions(self) -> None:
        assert hash(GoVersion("v1.2.3")) == hash(GoVersion("v1.2.3"))

    def test_ordering_is_by_version_string(self) -> None:
        assert GoVersion("v1.2.3") < GoVersion("v1.2.4")


class TestGoSpec(TestCase):
    """Tests for GoSpec."""

    def test_str_round_trips_the_version(self) -> None:
        assert str(GoSpec("v1.2.3")) == "v1.2.3"

    def test_a_leading_equals_is_parsed_away_for_membership(self) -> None:
        # str() keeps the original expression, but the parser strips the "=" so
        # "=v1.2.3" still matches the bare version.
        assert GoVersion("v1.2.3") in GoSpec("=v1.2.3")

    def test_contains_a_matching_version(self) -> None:
        assert GoVersion("v1.2.3") in GoSpec("v1.2.3")

    def test_does_not_contain_a_different_version(self) -> None:
        assert GoVersion("v1.2.4") not in GoSpec("v1.2.3")


class TestTagToGitHash(TestCase):
    """Tests for GoModule.tag_to_git_hash."""

    def test_returns_the_last_segment_of_a_three_part_tag(self) -> None:
        assert GoModule.tag_to_git_hash("v1.2.3-0-abcdef123456") == "abcdef123456"

    def test_returns_the_tag_unchanged_when_it_has_fewer_parts(self) -> None:
        assert GoModule.tag_to_git_hash("v1.2.3") == "v1.2.3"


class TestParseMod(TestCase):
    """Tests for GoModule.parse_mod."""

    def test_parses_a_module_line_alone(self) -> None:
        module = GoModule.parse_mod("module example.com/foo\n")
        assert module.name == "example.com/foo"
        assert module.dependencies == []

    def test_parses_a_single_require_line(self) -> None:
        module = GoModule.parse_mod(
            "module example.com/foo\nrequire github.com/bar/baz v1.2.3\n",
        )
        assert ("github.com/bar/baz", "v1.2.3") in module.dependencies

    def test_parses_a_require_block(self) -> None:
        module = GoModule.parse_mod(
            "module example.com/foo\n"
            "require (\n"
            "\tgithub.com/bar/baz v1.2.3\n"
            "\tgithub.com/qux/quux v0.1.0 // indirect\n"
            ")\n",
        )
        assert ("github.com/bar/baz", "v1.2.3") in module.dependencies
        assert ("github.com/qux/quux", "v0.1.0") in module.dependencies

    def test_accepts_bytes(self) -> None:
        module = GoModule.parse_mod(b"module example.com/foo\n")
        assert module.name == "example.com/foo"

    def test_raises_without_a_module_line(self) -> None:
        with pytest.raises(ValueError, match="Missing `module` line"):
            GoModule.parse_mod("require github.com/bar/baz v1.2.3\n")


class TestUrlForImportPath(TestCase):
    """Tests for GoModule.url_for_import_path."""

    def test_builds_a_go_get_url(self) -> None:
        assert (
            GoModule.url_for_import_path("github.com/foo/bar")
            == "https://github.com/foo/bar?go-get=1"
        )

    def test_raises_without_a_slash(self) -> None:
        with pytest.raises(VCSResolutionError, match="does not contain a slash"):
            GoModule.url_for_import_path("noslash")

    def test_raises_when_the_host_has_no_dot(self) -> None:
        with pytest.raises(VCSResolutionError, match="does not begin with hostname"):
            GoModule.url_for_import_path("localhost/foo")


class TestParseMetaGoImports(TestCase):
    """Tests for GoModule.parse_meta_go_imports."""

    def test_parses_a_go_import_meta_tag(self) -> None:
        html = (
            '<html><head><meta name="go-import" '
            'content="example.com/foo git https://example.com/foo.git">'
            "</head></html>"
        )
        imports = GoModule.parse_meta_go_imports(html)
        assert imports == [
            MetaImport(prefix="example.com/foo", vcs="git", repo_root="https://example.com/foo.git"),
        ]

    def test_ignores_meta_tags_with_a_different_name(self) -> None:
        html = '<meta name="description" content="example.com/foo git https://x.git">'
        assert GoModule.parse_meta_go_imports(html) == []

    def test_ignores_go_import_tags_without_exactly_three_fields(self) -> None:
        html = '<meta name="go-import" content="example.com/foo git">'
        assert GoModule.parse_meta_go_imports(html) == []

    def test_returns_nothing_for_html_without_meta_tags(self) -> None:
        assert GoModule.parse_meta_go_imports("<html><body>hi</body></html>") == []


class TestMatchGoImport(TestCase):
    """Tests for GoModule.match_go_import."""

    def test_matches_a_prefix(self) -> None:
        meta = MetaImport(prefix="github.com/foo/bar", vcs="git", repo_root="https://x.git")
        assert GoModule.match_go_import([meta], "github.com/foo/bar/baz") is meta

    def test_raises_when_nothing_matches(self) -> None:
        meta = MetaImport(prefix="github.com/other", vcs="git", repo_root="https://x.git")
        with pytest.raises(ValueError, match="Unable to match import path"):
            GoModule.match_go_import([meta], "github.com/foo/bar")

    def test_raises_on_multiple_matches(self) -> None:
        first = MetaImport(prefix="github.com/foo", vcs="git", repo_root="https://a.git")
        second = MetaImport(prefix="github.com/foo/bar", vcs="git", repo_root="https://b.git")
        with pytest.raises(ValueError, match="Multiple meta tags match"):
            GoModule.match_go_import([first, second], "github.com/foo/bar/baz")
