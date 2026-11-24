"""Generated documents preserve identities and constrain candidate dependency edges."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory

from hypothesis import given, settings
from hypothesis import strategies as st

from it_depends.explorer.loaders import load_saved


@settings(max_examples=40)
@given(st.lists(st.tuples(st.integers(0, 3), st.integers(0, 5), st.integers(0, 5)), unique=True, max_size=15))
def test_native_edges_include_exactly_matching_saved_versions(versions: list[tuple[int, int, int]]) -> None:
    root = {"name": "root", "version": "1.0.0", "source": "pip", "dependencies": {"pip:child": ">=1.0.0,<2.0.0"}}
    document = {"pip:root": {"1.0.0": root}, "pip:child": {}}
    for version in versions:
        text = ".".join(map(str, version))
        document["pip:child"][text] = {"name": "child", "version": text, "source": "pip"}
    with TemporaryDirectory() as directory:
        path = Path(directory) / "native.json"
        path.write_text(json.dumps(document))
        graph = load_saved(path)
    node = next(node for node in graph.nodes.values() if node.name == "root")
    matched = {graph.nodes[target].version for target in node.edges[0].targets}
    assert matched == {".".join(map(str, version)) for version in versions if version[0] == 1}


@settings(max_examples=40)
@given(st.lists(st.tuples(st.text(max_size=20), st.text(max_size=20)), unique_by=lambda item: item[0], max_size=12))
def test_cyclonedx_references_and_version_text_are_never_reinterpreted(components: list[tuple[str, str]]) -> None:
    document = {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "components": [
            {"name": f"package-{index}", "bom-ref": reference, "version": version}
            for index, (reference, version) in enumerate(components)
        ],
    }
    with TemporaryDirectory() as directory:
        path = Path(directory) / "cyclonedx.json"
        path.write_text(json.dumps(document))
        graph = load_saved(path)
    assert {(node.reference, node.version) for node in graph.nodes.values()} == set(components)
