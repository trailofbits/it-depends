"""Load native JSON and the component/dependency subset of CycloneDX JSON offline."""

from __future__ import annotations

import json
from collections import deque
from dataclasses import replace
from typing import TYPE_CHECKING, Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, JsonValue, TypeAdapter, ValidationError
from semantic_version import NpmSpec, SimpleSpec, Version

from it_depends.cargo import CargoSpec
from it_depends.explorer.model import SavedEdge, SavedGraph, SavedNode, saved_graph
from it_depends.go import GoSpec, GoVersion
from it_depends.npm import parse_npm_spec

if TYPE_CHECKING:
    from pathlib import Path


class _NativePackage(BaseModel):
    model_config = ConfigDict(strict=True)

    name: str
    version: str
    source: str
    dependencies: dict[str, str] = Field(default_factory=dict)
    vulnerabilities: list[str] = Field(default_factory=list)
    maintenance: dict[str, JsonValue] = Field(default_factory=dict)
    is_source_package: bool = False


class _Property(BaseModel):
    name: str = ""
    value: str | None = None


class _Component(BaseModel):
    model_config = ConfigDict(strict=True)

    name: str
    version: str = ""
    group: str = ""
    purl: str = ""
    bom_ref: str | None = Field(default=None, alias="bom-ref")
    properties: list[_Property] = Field(default_factory=list)
    components: list[_Component] = Field(default_factory=list)


class _Relationship(BaseModel):
    ref: str
    dependsOn: list[str] = Field(default_factory=list)  # noqa: N815 - CycloneDX field name.


class _Metadata(BaseModel):
    component: _Component | None = None


class _VersionStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    selector: str = Field(validation_alias=AliasChoices("version", "range"))
    status: Literal["affected", "unaffected", "unknown"] = "affected"


class _Affected(BaseModel):
    ref: str
    versions: list[_VersionStatus] = Field(default_factory=list)

    def describe(self, identifier: str) -> str:
        return "\n".join([identifier, *(f"  {entry.selector} ({entry.status})" for entry in self.versions)])


class _Vulnerability(BaseModel):
    id: str = "Unidentified vulnerability"
    affects: list[_Affected] = Field(default_factory=list)


class _CycloneDX(BaseModel):
    model_config = ConfigDict(strict=True)

    bomFormat: Literal["CycloneDX"]  # noqa: N815 - CycloneDX field name.
    specVersion: Literal["1.2", "1.3", "1.4", "1.5", "1.6", "1.7"]  # noqa: N815
    metadata: _Metadata = Field(default_factory=_Metadata)
    components: list[_Component] = Field(default_factory=list)
    dependencies: list[_Relationship] = Field(default_factory=list)
    vulnerabilities: list[_Vulnerability] = Field(default_factory=list)


_NATIVE = TypeAdapter(dict[str, dict[str, _NativePackage]])
_ECOSYSTEMS = frozenset({"pip", "npm", "cargo", "go", "ubuntu", "cmake", "autotools"})


def _properties(values: dict[str, JsonValue]) -> tuple[tuple[str, str], ...]:
    return tuple((name, str(value)) for name, value in values.items() if value is not None)


def _npm_matches(constraint: str, packages: dict[str, _NativePackage]) -> tuple[set[str], set[str]]:
    specification = parse_npm_spec(constraint)
    versions = {text: Version(text) for text in packages}
    matches = {text for text, version in versions.items() if version in specification}
    if isinstance(specification, NpmSpec):
        try:
            alternate = SimpleSpec(str(specification))
        except ValueError:
            return matches, set()
        alternate_matches = {text for text, version in versions.items() if version in alternate}
        return matches & alternate_matches, matches ^ alternate_matches
    return matches, set()


def _native_matches(
    group: str,
    constraint: str,
    packages: dict[str, _NativePackage],
) -> tuple[tuple[str, ...], str]:
    source = group.partition(":")[0]
    if source not in _ECOSYSTEMS:
        return (), f"Version matching is unavailable for ecosystem {source!r}"
    try:
        ambiguous: set[str] = set()
        if source == "npm":
            matches, ambiguous = _npm_matches(constraint, packages)
        else:
            specification = {"cargo": CargoSpec, "go": GoSpec}.get(source, SimpleSpec)(constraint)
            version_parser = GoVersion if source == "go" else Version
            matches = {text for text in packages if version_parser(text) in specification}
    except ValueError as error:
        return (), f"Cannot match saved constraint {constraint!r}: {error}"
    targets = tuple(json.dumps([group, text]) for text in packages if text in matches)
    if ambiguous:
        return targets, "The saved file omits the npm parser. Possible additional matches: " + ", ".join(
            sorted(ambiguous)
        )
    return targets, "" if targets else "No matching version is present in this document"


def _native(document: object) -> SavedGraph:
    packages = _NATIVE.validate_python(document)
    nodes: dict[str, SavedNode] = {}
    matches: dict[tuple[str, str], tuple[tuple[str, ...], str]] = {}
    for group, versions in packages.items():
        for version, package in versions.items():
            if group != f"{package.source}:{package.name}" or version != package.version:
                msg = f"Inconsistent package identity for {group}@{version}"
                raise ValueError(msg)
            edges = []
            for target, constraint in package.dependencies.items():
                key = (target, constraint)
                if key not in matches:
                    matches[key] = _native_matches(target, constraint, packages.get(target, {}))
                targets, note = matches[key]
                edges.append(SavedEdge(target, targets, constraint, note))
            node_id = json.dumps([group, version])
            nodes[node_id] = SavedNode(
                node_id=node_id,
                name=package.name,
                version=version,
                ecosystem=package.source,
                edges=tuple(edges),
                vulnerabilities=tuple(package.vulnerabilities),
                maintenance=_properties(package.maintenance),
                root=package.is_source_package,
            )
    return saved_graph("All candidate dependency versions", nodes)


def _components(bom: _CycloneDX) -> dict[str, _Component]:
    pending = deque(bom.components)
    if bom.metadata.component is not None:
        pending.appendleft(bom.metadata.component)
    components: dict[str, _Component] = {}
    anonymous = 0
    while pending:
        component = pending.popleft()
        if bom.specVersion in {"1.6", "1.7"} and any(
            "name" not in prop.model_fields_set for prop in component.properties
        ):
            msg = f"CycloneDX {bom.specVersion} requires a property name on component {component.name!r}"
            raise ValueError(msg)
        key = json.dumps(["ref", component.bom_ref])
        if component.bom_ref is None:
            key = json.dumps(["anonymous", anonymous])
            anonymous += 1
        existing = components.get(key)
        if existing is not None:
            if existing != component:
                msg = f"Conflicting components use bom-ref {component.bom_ref!r}"
                raise ValueError(msg)
            continue
        components[key] = component
        pending.extend(component.components)
    return components


def _component_node(key: str, component: _Component, root: _Component | None) -> SavedNode:
    source, separator, _ = (component.bom_ref or "").partition(":")
    name = f"{component.group}/{component.name}" if component.group else component.name
    maintenance = tuple(
        (prop.name.removeprefix("maintenance:"), prop.value)
        for prop in component.properties
        if prop.name.startswith("maintenance:") and prop.value is not None
    )
    return SavedNode(
        node_id=key,
        name=name,
        version=component.version,
        ecosystem=source if separator and source in _ECOSYSTEMS else "",
        reference=component.bom_ref or "",
        purl=component.purl,
        maintenance=maintenance,
        root=component is root,
    )


def _reference(nodes: dict[str, SavedNode], reference: str) -> str:
    key = json.dumps(["ref", reference])
    if key not in nodes:
        nodes[key] = SavedNode(node_id=key, name=reference, reference=reference, missing=True)
    return key


def _cyclonedx(document: object) -> SavedGraph:
    bom = _CycloneDX.model_validate(document)
    nodes = {
        key: _component_node(key, component, bom.metadata.component) for key, component in _components(bom).items()
    }
    for relationship in bom.dependencies:
        key = _reference(nodes, relationship.ref)
        edges = tuple(SavedEdge(target, (_reference(nodes, target),)) for target in relationship.dependsOn)
        nodes[key] = replace(nodes[key], edges=nodes[key].edges + edges)
    for vulnerability in bom.vulnerabilities:
        for affected in vulnerability.affects:
            key = _reference(nodes, affected.ref)
            nodes[key] = replace(
                nodes[key],
                vulnerabilities=(*nodes[key].vulnerabilities, affected.describe(vulnerability.id)),
            )
    return saved_graph(f"CycloneDX {bom.specVersion}", nodes)


def load_saved(path: Path) -> SavedGraph:
    """Read a saved graph without resolver execution, network access, or file changes.

    Args:
        path: A native it-depends JSON or CycloneDX JSON file.

    Returns:
        An immutable graph with saved metadata and dependency references.

    Raises:
        ValueError: The file is unreadable, malformed, or has an unsupported format.

    """
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(document, dict) and "bomFormat" in document:
            return _cyclonedx(document)
        return _native(document)
    except (OSError, UnicodeError, ValueError, ValidationError) as error:
        message = (
            f"Cannot explore {str(path)!r}: {error}. "
            "Provide native it-depends JSON or supported CycloneDX JSON (1.2-1.7)."
        )
        raise ValueError(message) from error
