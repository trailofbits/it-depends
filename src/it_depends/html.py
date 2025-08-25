from typing import Dict, Optional, Set, Union

from .dependencies import DependencyGraph, Package, PackageCache

TEMPLATE: str = """<html>
<head>
<title>It-Depends | $TITLE</title>
<style type="text/css">
mynetwork {
    width: 100%;
    height: 100%;
    border: 1px solid lightgray;
}
</style>

<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/vis/4.16.1/vis.css" type="text/css" />
<script type="text/javascript" src="https://cdnjs.cloudflare.com/ajax/libs/vis/4.16.1/vis-network.min.js"> </script>
<center>
<h1>$TITLE</h1>
</center>
</head>


<body>
<div id = "mynetwork"></div>

<script type="text/javascript">

// initialize global variables.
var edges;
var nodes;
var network;
var container;
var options, data;


// This method is responsible for drawing the graph, returns the drawn network
function drawGraph() {
    var container = document.getElementById('mynetwork');

    // parsing and collecting nodes and edges from the python
    nodes = new vis.DataSet($NODES);
    edges = new vis.DataSet($EDGES);


    // adding nodes and edges to the graph
    data = {nodes: nodes, edges: edges};


    const options = {
        manipulation: false,
        height: "90%",
        physics: {
            hierarchicalRepulsion: {
              nodeDistance: 300,
            },
          },
        edges: {
            color: {
                inherit: false
            },
            arrows: {
                to: true,
            },
        },
        layout: {
            $LAYOUT
        }
    };



    network = new vis.Network(container, data, options);
    return network;

}

drawGraph();

</script>
</body>
</html>
"""


def graph_to_html(
    graph: Union[DependencyGraph, PackageCache],
    collapse_versions: bool = True,
    title: Optional[str] = None,
) -> str:
    if not isinstance(graph, DependencyGraph):
        graph = graph.to_graph()
    if collapse_versions:
        graph = graph.collapse_versions()

    if graph.source_packages:
        roots: Set[Package] = graph.source_packages  # type: ignore
    else:
        roots = graph.find_roots().roots

    if not graph.source_packages:
        layout = "improvedLayout: false"
    else:
        layout = "hierarchical: true"

    # sort the nodes and assign IDs to them (so they are in a deterministic order):
    node_ids: Dict[Package, int] = {}
    for node in sorted(graph):
        node_ids[node] = len(node_ids)

    nodes = []
    edges = []
    for package, node_id in node_ids.items():
        nodes.append({"id": node_id, "label": package.full_name})
        if package in roots:
            nodes[-1].update(
                {
                    "shape": "square",
                    "color": "red",
                    "borderWidth": 4,
                }
            )
        if package.vulnerabilities:
            nodes[-1].update({"color": "red"})
        if graph.source_packages:
            nodes[-1]["level"] = max(graph.shortest_path_from_root(package), 0)
        for pkg1, pkg2, *_ in graph.out_edges(package):  # type: ignore
            dep = graph.get_edge_data(pkg1, pkg2)["dependency"]
            if collapse_versions:
                # if we are collapsing versions, omit the version name
                dep_name = f"{dep.source}:{dep.package}"
            else:
                dep_name = str(dep)
            edges.append({"from": node_ids[pkg1], "to": node_ids[pkg2], "shape": "dot"})
            if dep_name != pkg2.full_name:
                edges[-1]["label"] = dep_name

    if title is None:
        source_packages = ", ".join(p.full_name for p in graph.source_packages)
        if not source_packages:
            title = "Dependency Graph"
        else:
            title = f"Dependency Graph for {source_packages}"

    return (
        TEMPLATE.replace("$NODES", repr(nodes))
        .replace("$EDGES", repr(edges))
        .replace("$TITLE", title)
        .replace("$LAYOUT", layout)
    )
