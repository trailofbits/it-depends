"""Rooted directed graph implementation."""

from __future__ import annotations

from typing import TYPE_CHECKING, Generic, TypeVar

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator

import networkx as nx

T = TypeVar("T")
R = TypeVar("R")


class RootedDiGraph(nx.DiGraph, Generic[T, R]):
    """A directed graph with root nodes."""

    root_type: type[R]

    def __init__(self, *args: object, **kwargs: object) -> None:
        """Initialize the rooted directed graph."""
        super().__init__(*args, **kwargs)
        self.roots: set[R] = set()
        self._all_pairs_shortest_paths: dict[T, dict[T, int]] | None = None
        self._shortest_path_from_root: dict[T, int] | None = None

    def __init_subclass__(cls, **kwargs: object) -> None:
        """Validate that subclasses assign a root_type."""
        if not hasattr(cls, "root_type") or cls.root_type is None:
            msg = f"{cls.__name__} must assign a `root_type` class variable"
            raise TypeError(msg)

    def shortest_path_from_root(self, node: T) -> int:
        """Return the shortest path from a root to node.

        If there are no roots in the graph or there is no path from a root, return -1.
        """
        if not self.roots:
            return -1
        if len(self.roots) > 1:
            path_lengths = [self.shortest_path_length(root, node) for root in self.roots]
            return min(length for length in path_lengths if length >= 0)
        if self._shortest_path_from_root is None:
            self._shortest_path_from_root = nx.single_source_shortest_path_length(self, next(iter(self.roots)))  # type: ignore[assignment]
        return self._shortest_path_from_root[node]

    def shortest_path_length(self, from_node: T | R, to_node: T) -> int:
        """Get shortest path length between two nodes."""
        if self._all_pairs_shortest_paths is None:
            self._all_pairs_shortest_paths = dict(nx.all_pairs_shortest_path_length(self))  # type: ignore[assignment]
        if (
            from_node not in self._all_pairs_shortest_paths or to_node not in self._all_pairs_shortest_paths[from_node]  # type: ignore[index]
        ):
            return -1
        return self._all_pairs_shortest_paths[from_node][to_node]  # type: ignore[index]

    def _handle_new_node(self, node: T) -> None:
        """Handle adding a new node."""
        if isinstance(node, self.root_type):
            self.roots.add(node)

    def _handle_removed_node(self, node: T) -> None:
        """Handle removing a node."""
        if isinstance(node, self.root_type):
            self.roots.remove(node)

    def add_node(self, node_for_adding: T, **attr: object) -> None:
        """Add a node to the graph."""
        self._handle_new_node(node_for_adding)
        super().add_node(node_for_adding, **attr)

    def add_nodes_from(self, nodes_for_adding: Iterable[T], **attr: object) -> None:  # noqa: ARG002
        """Add multiple nodes to the graph."""
        nodes = []
        for node in nodes_for_adding:
            self._handle_new_node(node)
            nodes.append(node)
        super().add_nodes_from(nodes)

    def add_edge(self, u_of_edge: T, v_of_edge: T, **attr: object) -> None:
        """Add an edge to the graph."""
        self._handle_new_node(u_of_edge)
        self._handle_new_node(v_of_edge)
        super().add_edge(u_of_edge, v_of_edge, **attr)

    def add_edges_from(self, ebunch_to_add: Iterable[tuple[T, T] | tuple[T, T, dict]], **attr: object) -> None:
        """Add multiple edges to the graph."""
        edges = []
        for u, v, *r in ebunch_to_add:
            self._handle_new_node(u)
            self._handle_new_node(v)
            edges.append((u, v, *r))
        super().add_edges_from(ebunch_to_add, **attr)

    def remove_node(self, node_for_removing: T) -> None:
        """Remove a node from the graph."""
        self._handle_removed_node(node_for_removing)
        super().remove_node(node_for_removing)

    def remove_nodes_from(self, nodes_for_removing: Iterable[T]) -> None:
        """Remove multiple nodes from the graph."""
        nodes = []
        for node in nodes_for_removing:
            self._handle_removed_node(node)
            nodes.append(node)
        super().remove_nodes_from(nodes)

    def find_roots(self) -> RootedDiGraph[T, T]:
        """Find root nodes in the graph."""
        graph: RootedDiGraph[T, T] = RootedDiGraph()
        graph.root_type = self.root_type  # type: ignore[assignment]
        graph.add_nodes_from(self.nodes)
        graph.add_edges_from(self.edges)
        graph.roots = {n for n, d in self.in_degree() if d == 0}  # type: ignore[assignment]
        return graph

    def __iter__(self) -> Iterator[T]:
        """Iterate over nodes in the graph."""
        yield from super().__iter__()

    def distance_to(self, graph: RootedDiGraph[T, R], *, normalize: bool = False) -> float:
        """Calculate distance to another graph."""
        return compare_rooted_graphs(self, graph, normalize)


def compare_rooted_graphs(
    graph1: RootedDiGraph[T, R], graph2: RootedDiGraph[T, R], *, normalize: bool = False
) -> float:
    """Calculate the edit distance between two rooted graphs.

    If normalize == False (the default), a value of zero means the graphs are identical, with increasing values
    corresponding to the difference between the graphs.

    If normalize == True, the returned value equals 1.0 iff the graphs are identical and values closer to zero if the
    graphs are less similar.
    """
    if not graph1.roots or not graph2.roots:
        msg = "Both graphs must have at least one root"
        raise ValueError(msg)
    nodes1 = {node for node in graph1 if node not in graph1.roots}
    nodes2 = {node for node in graph2 if node not in graph2.roots}
    common_nodes = nodes1 & nodes2
    not_in_2 = nodes1 - nodes2
    not_in_1 = nodes2 - nodes1
    distance = 0.0
    for node in common_nodes:
        d1 = graph1.shortest_path_from_root(node)
        d2 = graph2.shortest_path_from_root(node)
        if d1 != d2:
            distance += 1.0 / min(d1, d2) - 1.0 / max(d1, d2)
    for node in not_in_2:
        distance += 1.0 / max(graph1.shortest_path_from_root(node), 1)
    for node in not_in_1:
        distance += 1.0 / max(graph2.shortest_path_from_root(node), 1)
    if normalize:
        if distance > 0.0:
            # the graphs are not identical
            max_distance = sum(max(graph1.shortest_path_from_root(node), 1) for node in graph1) + sum(
                max(graph2.shortest_path_from_root(node), 1) for node in graph2
            )
            distance /= max_distance
        distance = 1.0 - distance
    return distance
