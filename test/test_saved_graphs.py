"""Offline saved-result import behavior and current-export round trips."""

import json
from pathlib import Path
from unittest.mock import Mock

import pytest
from semantic_version import Version

from it_depends.cache import PackageRepository
from it_depends.explorer.loaders import load_saved
from it_depends.explorer.model import SavedGraph, SavedNode
from it_depends.models import Dependency, MaintenanceInfo, Package, SourcePackage, Vulnerability
from it_depends.repository import SourceRepository
from it_depends.sbom import SBOM, cyclonedx_to_json


@pytest.fixture
def native_file(tmp_path: Path) -> Path:
    root = SourcePackage(
        "demo",
        Version("1.0.0"),
        SourceRepository(tmp_path),
        "pip",
        dependencies=(Dependency.from_string("pip:lib@>=1.0.0,<2.0.0"),),
    )
    root.maintenance_info = MaintenanceInfo(is_stale=False)
    child = Package(
        "lib",
        "1.0.0",
        "pip",
        dependencies=(Dependency.from_string("pip:demo@*"),),
        vulnerabilities=(Vulnerability("CVE-example", (), "example"),),
    )
    repo = PackageRepository()
    repo.extend([root, child, Package("lib", "2.0.0", "pip")])
    path = tmp_path / "native.json"
    path.write_text(json.dumps(repo.to_obj()))
    return path


@pytest.fixture
def cyclone_file(tmp_path: Path) -> Path:
    root = Package("demo", "1.0.0", "pip")
    child = Package("lib", "1.0.0", "pip")
    path = tmp_path / "cyclonedx.json"
    path.write_text(cyclonedx_to_json(SBOM([(root, child)], [root]).to_cyclonedx()))
    return path


def node_named(graph: SavedGraph, name: str, version: str = "1.0.0") -> SavedNode:
    return next(node for node in graph.nodes.values() if node.name == name and node.version == version)


def test_native_round_trip_preserves_versions_constraints_cycles_and_metadata(native_file: Path) -> None:
    graph = load_saved(native_file)
    root = node_named(graph, "demo")
    child = node_named(graph, "lib")
    assert root.root
    assert root.edges[0].targets == (child.node_id,)
    assert root.edges[0].constraint == ">=1.0.0,<2.0.0"
    assert child.edges[0].targets == (root.node_id,)
    assert graph.incoming[child.node_id] == (root.node_id,)
    assert child.vulnerabilities == ("CVE-example ()",)
    assert ("is_stale", "False") in root.maintenance
    expected_versions = 2
    assert len(graph.groups["pip:lib"]) == expected_versions


def test_cyclonedx_round_trip_preserves_root_and_explicit_relationships(cyclone_file: Path) -> None:
    graph = load_saved(cyclone_file)
    root = node_named(graph, "demo")
    child = node_named(graph, "lib")
    assert root.root
    document = json.loads(cyclone_file.read_text())
    assert root.reference == document["metadata"]["component"]["bom-ref"]
    assert root.edges[0].targets == (child.node_id,)
    assert graph.incoming[child.node_id] == (root.node_id,)


def test_disconnected_components_and_nonsemantic_versions_are_preserved(tmp_path: Path) -> None:
    path = tmp_path / "external.json"
    path.write_text(
        json.dumps(
            {
                "bomFormat": "CycloneDX",
                "specVersion": "1.6",
                "components": [
                    {"name": "alpha", "version": "release/blue", "bom-ref": "opaque-id"},
                    {"name": "beta", "bom-ref": "other-id"},
                ],
            }
        )
    )
    graph = load_saved(path)
    assert node_named(graph, "alpha", "release/blue").reference == "opaque-id"
    assert all(not node.edges for node in graph.nodes.values())
    assert not graph.incoming


def test_dangling_cyclonedx_references_are_visible(tmp_path: Path) -> None:
    path = tmp_path / "dangling.json"
    path.write_text(
        json.dumps(
            {
                "bomFormat": "CycloneDX",
                "specVersion": "1.5",
                "components": [{"name": "alpha", "bom-ref": "alpha"}],
                "dependencies": [{"ref": "alpha", "dependsOn": ["missing"]}],
                "vulnerabilities": [{"id": "CVE-example", "affects": [{"ref": "alpha"}]}],
            }
        )
    )
    graph = load_saved(path)
    alpha = node_named(graph, "alpha", "")
    target = graph.nodes[alpha.edges[0].targets[0]]
    assert target.missing
    assert target.reference == "missing"
    assert alpha.vulnerabilities == ("CVE-example",)


def test_native_unresolved_and_unparseable_constraints_are_explicit(native_file: Path) -> None:
    document = json.loads(native_file.read_text())
    document["pip:demo"]["1.0.0"]["dependencies"] = {"pip:absent": "*", "pip:lib": "invalid"}
    native_file.write_text(json.dumps(document))
    graph = load_saved(native_file)
    root = node_named(graph, "demo")
    assert all(not edge.targets for edge in root.edges)
    assert all(edge.note for edge in root.edges)


@pytest.mark.parametrize(
    "document",
    [
        None,
        [],
        {"wrong": "format"},
        {"bomFormat": "SPDX", "specVersion": "1.5"},
        {"bomFormat": "CycloneDX", "specVersion": "2.0"},
        {"pip:demo": {"1.0.0": {"name": "other", "version": "1.0.0", "source": "pip"}}},
        {
            "bomFormat": "CycloneDX",
            "specVersion": "1.5",
            "components": [
                {"name": "alpha", "bom-ref": "duplicate"},
                {"name": "beta", "bom-ref": "duplicate"},
            ],
        },
    ],
)
def test_malformed_or_unsupported_documents_are_actionable(tmp_path: Path, document: object) -> None:
    path = tmp_path / "invalid.json"
    path.write_text(json.dumps(document))
    with pytest.raises(ValueError, match="Cannot explore"):
        load_saved(path)


def test_missing_and_non_json_files_are_actionable(tmp_path: Path) -> None:
    path = tmp_path / "missing.json"
    with pytest.raises(ValueError, match="Cannot explore"):
        load_saved(path)
    path.write_text("not JSON")
    with pytest.raises(ValueError, match="Cannot explore"):
        load_saved(path)


def test_empty_native_document_is_valid(tmp_path: Path) -> None:
    path = tmp_path / "empty.json"
    path.write_text("{}")
    assert not load_saved(path).nodes


def test_loading_does_not_execute_commands_or_access_network(
    native_file: Path,
    cyclone_file: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    boundary = Mock(side_effect=AssertionError("offline exploration cannot execute external operations"))
    monkeypatch.setattr("subprocess.Popen", boundary)
    monkeypatch.setattr("requests.sessions.Session.request", boundary)
    assert load_saved(native_file).nodes
    assert load_saved(cyclone_file).nodes
    boundary.assert_not_called()
