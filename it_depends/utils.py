import webbrowser
import json
import sys
import functools
import gzip
import os
import re
import logging
import subprocess
from typing import Dict, List, Optional, Tuple
from urllib import request

logger = logging.getLogger(__name__)
all_packages: Optional[Tuple[str, ...]] = None

def get_apt_packages() -> Tuple[str, ...]:
    global all_packages
    if all_packages is None:
        logger.info("Rebuilding global apt package list.")
        raw_packages = subprocess.check_output(["apt", "list"], stderr=subprocess.DEVNULL).decode("utf8")
        all_packages = tuple(x.split("/")[0] for x in raw_packages.splitlines() if x)

        logger.info(f"Global apt package count {len(all_packages)}")
    return all_packages


def search_package(package: str) -> str:
    found_packages: List[str] = []
    for apt_package in get_apt_packages():
        if package.lower() not in apt_package:
            continue
        if re.match(
                fr"^(lib)*{re.escape(package.lower())}(\-*([0-9]*)(\.*))*(\-dev)*$",
                apt_package):
            found_packages.append(apt_package)
    found_packages.sort(key=len, reverse=True)
    if not found_packages:
        raise ValueError(f"Package {package} not found in apt package list.")
    logger.info(
        f"Found {len(found_packages)} matching packages, Choosing {found_packages[0]}")
    return found_packages[0]


contents_db: Dict[str, List[str]] = {}


@functools.lru_cache(maxsize=128)
def _file_to_package_contents(filename: str, arch: str = "amd64"):
    """
    Downloads and uses apt-file database directly
    # http://security.ubuntu.com/ubuntu/dists/focal-security/Contents-amd64.gz
    # http://security.ubuntu.com/ubuntu/dists/focal-security/Contents-i386.gz
    """
    if arch not in ("amd64", "i386"):
        raise ValueError("Only amd64 and i386 supported")
    selected = None

    # TODO find better location https://pypi.org/project/appdirs/?
    dbfile = os.path.join(os.path.dirname(__file__), f"Contents-{arch}.gz")
    if not os.path.exists(dbfile):
        request.urlretrieve(
            f"http://security.ubuntu.com/ubuntu/dists/focal-security/Contents-{arch}.gz",
            dbfile)
    if not contents_db:
        logger.info("Rebuilding contents db")
        with gzip.open(dbfile, "rt") as contents:
            for line in contents.readlines():
                filename_i, *packages_i = re.split(r"\s+", line[:-1])
                assert(len(packages_i) > 0)
                contents_db.setdefault(filename_i, []).extend(packages_i)

    regex = re.compile("(.*/)+"+filename+"$")
    matches = 0
    for (filename_i, packages_i) in contents_db.items():
        if regex.match(filename_i):
            matches += 1
            for package_i in packages_i:
                if selected is None or len(selected[0]) > len(filename_i):
                    selected = filename_i, package_i
    if selected:
        logger.info(
            f"Found {matches} matching packages for {filename}. Choosing {selected[1]}")
    else:
        raise ValueError(f"{filename} not found in Contents database")
    return selected[1]


@functools.lru_cache(maxsize=128)
def _file_to_package_apt_file(filename: str, arch: str = "amd64") -> str:
    if arch not in ("amd64", "i386"):
        raise ValueError("Only amd64 and i386 supported")
    logger.debug(f'Running [{" ".join(["apt-file", "-x", "search", filename])}]')
    contents = subprocess.run(["apt-file", "-x", "search", filename],
                              stdout=subprocess.PIPE).stdout.decode("utf8")
    db: Dict[str, str] = {}
    selected = None
    for line in contents.split("\n"):
        if not line:
            continue
        package_i, filename_i = line.split(": ")
        db[filename_i] = package_i
        if selected is None or len(selected[0]) > len(filename_i):
            selected = filename_i, package_i

    if selected:
        logger.info(
            f"Found {len(db)} matching packages for {filename}. Choosing {selected[1]}")
    else:
        raise ValueError(f"{filename} not found in apt-file")

    return selected[1]


@functools.lru_cache(maxsize=128)
def file_to_package(filename: str, arch: str = "amd64") -> str:
    filename = f"/{filename}$"
    return _file_to_package_apt_file(filename, arch=arch)


def cached_file_to_package(pattern: str, file_to_package_cache: Optional[List[Tuple[str, str]]] = None) -> str:
    # file_to_package_cache contains all the files that are provided be previous
    # dependencies. If a file pattern is already sastified by current files
    # use the package already included as a dependency
    if file_to_package_cache is not None:
        regex = re.compile("(.*/)+" + pattern + "$")
        for package_i, filename_i in file_to_package_cache:
            if regex.match(filename_i):
                return package_i

    package = file_to_package(pattern)

    # a new package is chosen add all the files it provides to our cache
    # uses `apt-file` command line tool
    if file_to_package_cache is not None:
        contents = subprocess.run(["apt-file", "list", package],
                                  stdout=subprocess.PIPE).stdout.decode("utf8")
        for line in contents.split("\n"):
            if ":" not in line:
                break
            package_i, filename_i = line.split(": ")
            file_to_package_cache.append((package_i, filename_i))

    return package

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
