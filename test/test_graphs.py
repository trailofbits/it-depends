import unittest

from it_depends.graphs import RootedDiGraph


class Node(int):
    pass


class Root(Node):
    pass


class TestGraphs(unittest.TestCase):
    def test_single_root(self) -> None:
        graph: RootedDiGraph[Node, Root] = RootedDiGraph()
        graph.root_type = Root
        nodes = [Root(0)] + [Node(i) for i in range(1, 5)]
        graph.add_node(nodes[0])
        graph.add_edge(nodes[0], nodes[1])
        graph.add_edge(nodes[0], nodes[2])
        graph.add_edge(nodes[1], nodes[3])
        graph.add_edge(nodes[2], nodes[4])
        assert graph.shortest_path_from_root(nodes[0]) == 0
        assert graph.shortest_path_from_root(nodes[1]) == 1
        assert graph.shortest_path_from_root(nodes[2]) == 1
        assert graph.shortest_path_from_root(nodes[3]) == 2  # noqa: PLR2004
        assert graph.shortest_path_from_root(nodes[4]) == 2  # noqa: PLR2004

    def test_two_roots(self) -> None:
        graph: RootedDiGraph[Node, Root] = RootedDiGraph()
        graph.root_type = Root
        nodes = [Root(0), Root(1)] + [Node(i) for i in range(2, 5)]
        graph.add_node(nodes[0])
        graph.add_node(nodes[1])
        graph.add_edge(nodes[0], nodes[2])
        graph.add_edge(nodes[0], nodes[3])
        graph.add_edge(nodes[1], nodes[3])
        graph.add_edge(nodes[3], nodes[4])
        assert graph.shortest_path_from_root(nodes[0]) == 0
        assert graph.shortest_path_from_root(nodes[1]) == 0
        assert graph.shortest_path_from_root(nodes[2]) == 1
        assert graph.shortest_path_from_root(nodes[3]) == 1
        assert graph.shortest_path_from_root(nodes[4]) == 2  # noqa: PLR2004

    def test_add_edges_from_generator(self) -> None:
        # add_edges_from must accept a single-pass iterable (a generator), as
        # the NetworkX contract allows. The loop that registers root nodes
        # consumes the iterable, so super() has to receive the materialized
        # edges, not the exhausted original.
        graph: RootedDiGraph[Node, Root] = RootedDiGraph()
        graph.root_type = Root
        edges = [(Root(0), Node(1)), (Node(1), Node(2))]
        graph.add_edges_from((u, v) for u, v in edges)
        assert graph.number_of_edges() == len(edges)
        assert set(graph.edges) == set(edges)
        assert Root(0) in graph.roots

    def test_add_edges_from_generator_with_attrs(self) -> None:
        graph: RootedDiGraph[Node, Root] = RootedDiGraph()
        graph.root_type = Root
        graph.add_edges_from((u, v, d) for u, v, d in [(Root(0), Node(1), {"w": 2})])
        assert graph.number_of_edges() == 1
        assert graph[Root(0)][Node(1)]["w"] == 2  # noqa: PLR2004

    def test_add_nodes_from_generator(self) -> None:
        graph: RootedDiGraph[Node, Root] = RootedDiGraph()
        graph.root_type = Root
        nodes = [Root(0), Node(1), Node(2)]
        graph.add_nodes_from(n for n in nodes)
        assert set(graph.nodes) == set(nodes)
        assert Root(0) in graph.roots
