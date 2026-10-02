"""Tests for HTML graph generation."""

from unittest import TestCase

from it_depends.dependencies import (
    Dependency,
    InMemoryPackageCache,
    Package,
    SimpleSpec,
    Version,
)
from it_depends.html import TEMPLATE, graph_to_html


class TestGraphToHtml(TestCase):
    """Tests for graph_to_html."""

    def _make_pkg(
        self,
        name: str = "foo",
        version: str = "1.0.0",
        dependencies: tuple[Dependency, ...] = (),
        vulnerabilities: tuple[object, ...] = (),
    ) -> Package:
        return Package(
            name=name,
            version=Version.coerce(version),
            source="pip",
            dependencies=dependencies,
            vulnerabilities=vulnerabilities,  # type: ignore[arg-type]
        )

    def _cache_with_two_packages(self) -> InMemoryPackageCache:
        cache = InMemoryPackageCache()
        cache.add(
            self._make_pkg(
                name="foo",
                dependencies=(Dependency(package="bar", semantic_version=SimpleSpec(">=1.0.0"), source="pip"),),
            )
        )
        cache.add(self._make_pkg(name="bar"))
        return cache

    def test_returns_html_document(self) -> None:
        html = graph_to_html(self._cache_with_two_packages(), title="t")
        assert html.startswith("<html>")
        assert html.rstrip().endswith("</html>")

    def test_all_template_placeholders_are_substituted(self) -> None:
        # A leftover placeholder means a branch of graph_to_html never ran.
        html = graph_to_html(self._cache_with_two_packages(), title="t")
        for token in ("$NODES", "$EDGES", "$TITLE", "$LAYOUT"):
            assert token not in html, f"{token} was not substituted"

    def test_title_is_substituted(self) -> None:
        html = graph_to_html(self._cache_with_two_packages(), title="My Graph")
        assert "<title>It-Depends | My Graph</title>" in html
        assert "<h1>My Graph</h1>" in html

    def test_default_title_without_source_packages(self) -> None:
        html = graph_to_html(self._cache_with_two_packages())
        assert "Dependency Graph" in html

    def test_accepts_a_package_cache_not_only_a_graph(self) -> None:
        # graph_to_html calls .to_graph() when handed a cache.
        cache = self._cache_with_two_packages()
        from_cache = graph_to_html(cache, title="t")
        from_graph = graph_to_html(cache.to_graph(), title="t")
        assert from_cache == from_graph

    def test_collapse_versions_changes_the_rendered_edges(self) -> None:
        cache = self._cache_with_two_packages()
        collapsed = graph_to_html(cache, collapse_versions=True, title="t")
        uncollapsed = graph_to_html(cache, collapse_versions=False, title="t")
        # Both must render; the edge labels differ because dep_name switches
        # between "source:package" and str(dep).
        assert "$EDGES" not in collapsed
        assert "$EDGES" not in uncollapsed

    def test_empty_cache_still_renders_a_document(self) -> None:
        html = graph_to_html(InMemoryPackageCache(), title="empty")
        assert html.startswith("<html>")
        assert "<h1>empty</h1>" in html

    def test_layout_is_improved_when_there_are_no_source_packages(self) -> None:
        html = graph_to_html(self._cache_with_two_packages(), title="t")
        assert "improvedLayout: false" in html

    def test_nodes_carry_a_label_per_collapsed_package(self) -> None:
        html = graph_to_html(self._cache_with_two_packages(), title="t")
        assert "'id': 0, 'label': 'pip:bar'" in html
        assert "'id': 1, 'label': 'pip:foo'" in html

    def test_source_packages_are_drawn_as_roots(self) -> None:
        # The source package (foo) gets root styling; the plain dependency does not.
        html = graph_to_html(self._cache_with_two_packages(), title="t")
        assert "'shape': 'square', 'color': 'red', 'borderWidth': 4" in html

    def test_a_dependency_matching_its_package_name_gets_no_edge_label(self) -> None:
        # dep_name == pkg2.full_name here, so the label branch is skipped.
        html = graph_to_html(self._cache_with_two_packages(), title="t")
        assert "edges = new vis.DataSet([{'from': 1, 'to': 0, 'shape': 'dot'}]);" in html

    def test_uncollapsed_edges_carry_a_label(self) -> None:
        # With collapse_versions=False, dep_name becomes str(dep) and differs from
        # the package name, so the edge is labelled.
        html = graph_to_html(self._cache_with_two_packages(), collapse_versions=False, title="t")
        assert "'label':" in html.split("edges = new vis.DataSet(", 1)[1]

    def test_template_is_a_string_constant(self) -> None:
        assert isinstance(TEMPLATE, str)
        assert "$NODES" in TEMPLATE
