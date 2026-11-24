"""Immutable display models that do not reconstruct executable resolver objects."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping


@dataclass(frozen=True)
class SavedEdge:
    """A saved declaration and the matching components present in the document."""

    name: str
    targets: tuple[str, ...]
    constraint: str = ""
    note: str = ""


@dataclass(frozen=True, kw_only=True)
class SavedNode:
    """One component with an opaque identity and unmodified version text."""

    node_id: str
    name: str
    version: str = ""
    ecosystem: str = ""
    reference: str = ""
    purl: str = ""
    edges: tuple[SavedEdge, ...] = ()
    vulnerabilities: tuple[str, ...] = ()
    maintenance: tuple[tuple[str, str], ...] = ()
    root: bool = False
    missing: bool = False

    @property
    def group(self) -> str:
        """Return the package grouping label without interpreting its version."""
        return f"{self.ecosystem}:{self.name}" if self.ecosystem else self.name

    @property
    def label(self) -> str:
        """Return a readable component label."""
        return f"{self.group}@{self.version}" if self.version else self.group


@dataclass(frozen=True)
class SavedGraph:
    """A read-only graph with indexed package groups and incoming relationships."""

    kind: str
    nodes: Mapping[str, SavedNode]
    groups: Mapping[str, tuple[str, ...]]
    incoming: Mapping[str, tuple[str, ...]]


def saved_graph(kind: str, nodes: dict[str, SavedNode]) -> SavedGraph:
    """Index a loaded document without inferring additional dependency relationships."""
    groups: dict[str, list[str]] = defaultdict(list)
    incoming: dict[str, set[str]] = defaultdict(set)
    for node in nodes.values():
        groups[node.group].append(node.node_id)
        for edge in node.edges:
            for target in edge.targets:
                incoming[target].add(node.node_id)
    return SavedGraph(
        kind,
        MappingProxyType(nodes),
        MappingProxyType({name: tuple(ids) for name, ids in sorted(groups.items())}),
        MappingProxyType({name: tuple(sorted(ids)) for name, ids in incoming.items()}),
    )
