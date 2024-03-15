from typing import (
    Dict,
    Generic,
    Iterable,
    Iterator,
    Optional,
    Set,
    Tuple,
    Type,
    TypeVar,
    Union,
)

import networkx as nx


T = TypeVar("T")
R = TypeVar("R")


class RootedDiGraph(nx.DiGraph, Generic[T, R]):
    root_type: Type[R]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.roots: Set[R] = set()
        self._all_pairs_shortest_paths: Optional[Dict[T, Dict[T, int]]] = None
        self._shortest_path_from_root: Optional[Dict[T, int]] = None

    def __init_subclass__(cls, **kwargs):
        if not hasattr(cls, "root_type") or getattr(cls, "root_type") is None:
            raise TypeError(f"{cls.__name__} must assign a `root_type` class variable")

    def shortest_path_from_root(self, node: T) -> int:
        """Returns the shortest path from a root to node.

        If there are no roots in the graph or there is no path from a root, return -1.

        """
        if not self.roots:
            return -1
        if len(self.roots) > 1:
            path_lengths = [self.shortest_path_length(root, node) for root in self.roots]
            return min(length for length in path_lengths if length >= 0)
        elif self._shortest_path_from_root is None:
            self._shortest_path_from_root = nx.single_source_shortest_path_length(
                self, next(iter(self.roots))
            )  # type: ignore
        return self._shortest_path_from_root[node]

    def shortest_path_length(self, from_node: Union[T, R], to_node: T) -> int:
        if self._all_pairs_shortest_paths is None:
            self._all_pairs_shortest_paths = dict(nx.all_pairs_shortest_path_length(self))  # type: ignore
        if (
            from_node not in self._all_pairs_shortest_paths
            or to_node not in self._all_pairs_shortest_paths[from_node]  # type: ignore
        ):  # type: ignore
            return -1
        return self._all_pairs_shortest_paths[from_node][to_node]  # type: ignore

    def _handle_new_node(self, node: T):
        if isinstance(node, self.root_type):
            self.roots.add(node)

    def _handle_removed_node(self, node: T):
        if isinstance(node, self.root_type):
            self.roots.remove(node)

    def add_node(self, node_for_adding: T, **attr):
        self._handle_new_node(node_for_adding)
        return super().add_node(node_for_adding, **attr)

    def add_nodes_from(self, nodes_for_adding: Iterable[T], **attr):
        nodes = []
        for node in nodes_for_adding:
            self._handle_new_node(node)
            nodes.append(node)
        return super().add_nodes_from(nodes)

    def add_edge(self, u_of_edge: T, v_of_edge: T, **attr):
        self._handle_new_node(u_of_edge)
        self._handle_new_node(v_of_edge)
        return super().add_edge(u_of_edge, v_of_edge, **attr)

    def add_edges_from(
        self, ebunch_to_add: Iterable[Union[Tuple[T, T], Tuple[T, T, Dict]]], **attr
    ):
        edges = []
        for u, v, *r in ebunch_to_add:
            self._handle_new_node(u)
            self._handle_new_node(v)
            edges.append((u, v, *r))
        super().add_edges_from(ebunch_to_add, **attr)

    def remove_node(self, node_for_removing: T):
        self._handle_removed_node(node_for_removing)
        return super().remove_node(node_for_removing)

    def remove_nodes_from(self, nodes_for_removing: Iterable[T]):
        nodes = []
        for node in nodes_for_removing:
            self._handle_removed_node(node)
            nodes.append(node)
        return super().remove_nodes_from(nodes)

    def find_roots(self) -> "RootedDiGraph[T, T]":
        graph: RootedDiGraph[T, T] = RootedDiGraph()
        graph.root_type = self.root_type  # type: ignore
        graph.add_nodes_from(self.nodes)
        graph.add_edges_from(self.edges)
        graph.roots = {n for n, d in self.in_degree() if d == 0}  # type: ignore
        return graph

    def __iter__(self) -> Iterator[T]:
        yield from super().__iter__()

    def distance_to(self, graph: "RootedDiGraph[T, R]", normalize: bool = False) -> float:
        return compare_rooted_graphs(self, graph, normalize)


def compare_rooted_graphs(
    graph1: RootedDiGraph[T, R], graph2: RootedDiGraph[T, R], normalize: bool = False
) -> float:
    """Calculates the edit distance between two rooted graphs.

    If normalize == False (the default), a value of zero means the graphs are identical, with increasing values
    corresponding to the difference between the graphs.

    If normalize == True, the returned value equals 1.0 iff the graphs are identical and values closer to zero if the
    graphs are less similar.

    """
    if not graph1.roots or not graph2.roots:
        raise ValueError("Both graphs must have at least one root")
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
            max_distance = sum(
                max(graph1.shortest_path_from_root(node), 1) for node in graph1
            ) + sum(max(graph2.shortest_path_from_root(node), 1) for node in graph2)
            distance /= max_distance
        distance = 1.0 - distance
    return distance
