import os
import argparse
from contextlib import contextmanager
import json
import sys
from typing import Iterator, Optional, Sequence, TextIO
import webbrowser

from .db import DEFAULT_DB_PATH, DBPackageCache
from .dependencies import resolvers, resolve, SourceRepository

# TODO: felipe
# Low quality / Low doc code follows...
def get_graph(db, nodes, edges):
    if nodes == None:
        nodes = {}
    if edges == None:
        edges = {}

    def add_node(node_label, **options):
        if node_label not in nodes:
            nodes[node_label] = {'id': len(nodes), 'label': node_label}
            nodes[node_label].update(options)
        return nodes[node_label]

    def add_edge(label_orig, label_dest, **options):
        node_orig = add_node(label_orig, **options)
        node_dest = add_node(label_dest, **options)
        edge = (label_orig, label_dest)
        if edge not in edges:
            edges[edge] = {'from': node_orig['id'], 'to': node_dest['id']}
            edges[edge].update(options)

        return edges[edge]

    for package in db:
        for version in db[package]:
            options = {'shape': 'dot'}
            if db[package][version].get('is_source_package', False):
                options['shape'] = "square"
                options['color'] = "red"
                options['borderWidth'] = 4

            add_node(package, **options)

    for package in db:
        for version in db[package]:
            for dest in db[package][version]['dependencies'].keys():
                if ":" in dest:
                    _, dest = dest.split(":", 1)
                add_edge(package, dest, shape='dot')
    return nodes, edges


def get_html_graph(nodes, edges):
    def nodes_to_str(nodes):
        return repr(list(nodes.values()))
        # [{"id": 0, "label": "libfreetype6", "shape": "dot"}]

    def edges_to_str(edges):
        return repr(list(edges.values()))
        # {"from": 113, "to": 1}

    # html = open("template.html").read()
    html = """<html>
    <head>
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
    <h1>Dependency Graph</h1>
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
            },
            layout: {
                improvedLayout: false
            }
        };



        network = new vis.Network(container, data, options);
        return network;

    }

    drawGraph();

    </script>
    </body>
    </html>"""
    html = html.replace("$NODES", nodes_to_str(nodes))
    html = html.replace("$EDGES", edges_to_str(edges))
    return html


def show_graph(db, output_name="output.html"):
    nodes = {}
    edges = {}
    html = get_html_graph(*get_graph(db=db, nodes=nodes, edges=edges))

    with open(output_name, "w+") as out:
        out.write(html)

    webbrowser.open(output_name)


def show_graph_from_cmdline(db):
    nodes = {}
    edges = {}
    for json_filename in sys.argv[1:]:
        get_graph(db=json.load(open(json_filename)), nodes=nodes, edges=edges)

    html = get_html_graph(nodes, edges)
    name = "output.html"
    with open(name, "w+") as out:
        out.write(html)

    webbrowser.open(name)

@contextmanager
def no_stdout() -> Iterator[TextIO]:
    """A context manager that redirects STDOUT to STDERR"""
    saved_stdout = sys.stdout
    sys.stdout = sys.stderr
    try:
        yield saved_stdout
    finally:
        sys.stdout = saved_stdout


def main(argv: Optional[Sequence[str]] = None) -> int:
    if argv is None:
        argv = sys.argv

    parser = argparse.ArgumentParser(description="a source code dependency analyzer")

    parser.add_argument("PATH", nargs="?", type=str, default=".", help="path to the directory to analyze")
    parser.add_argument("--list", "-l", action="store_true", help="list available package classifiers")
    parser.add_argument("--database", "-db", type=str, nargs="?", default=DEFAULT_DB_PATH,
                        help="alternative path to load/store the database, or \":memory:\" to cache all results in "
                             f"memory rather than reading/writing to disk (default is {DEFAULT_DB_PATH!s})")
    parser.add_argument("--output-format", "-f", choices=("json", "dot", "html"), default="json",
                        help="how the output should be formatted (default is JSON)")
    parser.add_argument("--depth-limit", "-d", type=int, default=-1,
                        help="depth limit for recursively solving dependencies (default is -1 to resolve all "
                             "dependencies)")
    parser.add_argument("--max-workers", "-j", type=int, default=None, help="maximum number of jobs to run concurrently"
                                                                            " (default is # of CPUs)")

    args = parser.parse_args(argv[1:])

    if args.list:
        sys.stdout.flush()
        sys.stderr.write(f"Available resolvers for {os.path.abspath(args.PATH)}:\n")
        sys.stderr.flush()
        for name, classifier in sorted((c.name, c) for c in resolvers()):
            sys.stdout.write(name + " "*(12-len(name)))
            sys.stdout.flush()
            available = classifier.is_available()
            if not available:
                sys.stderr.write(f"\tnot available: {available.reason}")
                sys.stderr.flush()
            elif not classifier.can_resolve_from_source(SourceRepository(args.PATH)):
                sys.stderr.write("\tincompatible with this path")
                sys.stderr.flush()
            else:
                sys.stderr.write("\tenabled")
                sys.stderr.flush()

            sys.stdout.write("\n")
            sys.stdout.flush()
        return 0

    with no_stdout() as real_stdout:
        with DBPackageCache(args.database) as cache:
            # TODO: Add support for searching by package name
            repo = SourceRepository(args.PATH)

            package_list = resolve(repo, cache=cache, depth_limit=args.depth_limit, max_workers=args.max_workers)
            if not package_list:
                real_stdout.write(f"Try --list to check for available resolvers for {args.PATH}")

            if args.output_format == "dot":
                real_stdout.write(cache.to_dot(package_list.source_packages).source)
            if args.output_format == "html":
                show_graph(package_list.to_obj())
            elif args.output_format == "json":
                real_stdout.write(json.dumps(package_list.to_obj(), indent=4))
            else:
                raise NotImplementedError(f"TODO: Implement output format {args.output_format}")

    return 0
