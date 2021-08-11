from typing import Dict, Generic, Iterable, Iterator, Set, Tuple, Type, TypeVar, Union

import networkx as nx


T = TypeVar("T")
R = TypeVar("R")


class RootedDiGraph(nx.DiGraph, Generic[T, R]):
    key_type: Type[R]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.roots: Set[R] = set()

    def __init_subclass__(cls, **kwargs):
        if not hasattr(cls, "key_type") or getattr(cls, "key_type") is None:
            raise TypeError(f"{cls.__name__} must assign a `key_type` class variable")

    def _handle_new_node(self, node: T):
        if isinstance(node, self.key_type):
            self.roots.add(node)

    def _handle_removed_node(self, node: T):
        if isinstance(node, self.key_type):
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

    def add_edges_from(self, ebunch_to_add: Iterable[Union[Tuple[T, T], Tuple[T, T, Dict]]], **attr):
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

    def __iter__(self) -> Iterator[T]:
        yield from super().__iter__()
