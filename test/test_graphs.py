import unittest

from it_depends.graphs import RootedDiGraph

class Node(int):
    pass


class Root(Node):
    pass


class TestGraphs(unittest.TestCase):
    def test_single_root(self):
        graph: RootedDiGraph[Node, Root] = RootedDiGraph()
        graph.root_type = Root
        nodes = [Root(0)] + [Node(i) for i in range(1, 5)]
        graph.add_node(nodes[0])
        graph.add_edge(nodes[0], nodes[1])
        graph.add_edge(nodes[0], nodes[2])
        graph.add_edge(nodes[1], nodes[3])
        graph.add_edge(nodes[2], nodes[4])
        self.assertEqual(0, graph.shortest_path_from_root(nodes[0]))
        self.assertEqual(1, graph.shortest_path_from_root(nodes[1]))
        self.assertEqual(1, graph.shortest_path_from_root(nodes[2]))
        self.assertEqual(2, graph.shortest_path_from_root(nodes[3]))
        self.assertEqual(2, graph.shortest_path_from_root(nodes[4]))

    def test_two_roots(self):
        graph: RootedDiGraph[Node, Root] = RootedDiGraph()
        graph.root_type = Root
        nodes = [Root(0), Root(1)] + [Node(i) for i in range(2, 5)]
        graph.add_node(nodes[0])
        graph.add_node(nodes[1])
        graph.add_edge(nodes[0], nodes[2])
        graph.add_edge(nodes[0], nodes[3])
        graph.add_edge(nodes[1], nodes[3])
        graph.add_edge(nodes[3], nodes[4])
        self.assertEqual(0, graph.shortest_path_from_root(nodes[0]))
        self.assertEqual(0, graph.shortest_path_from_root(nodes[1]))
        self.assertEqual(1, graph.shortest_path_from_root(nodes[2]))
        self.assertEqual(1, graph.shortest_path_from_root(nodes[3]))
        self.assertEqual(2, graph.shortest_path_from_root(nodes[4]))
