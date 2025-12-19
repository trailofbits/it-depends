"""Software Bill of Materials (SBOM) generation module."""

from collections.abc import Iterable
from typing import TypeVar

from cyclonedx.builder.this import this_component as cdx_lib_component
from cyclonedx.model import XsUri
from cyclonedx.model.bom import Bom
from cyclonedx.model.component import Component, ComponentType, Property
from cyclonedx.model.contact import OrganizationalEntity
from cyclonedx.output.json import JsonV1Dot5

from . import __version__ as version
from .dependencies import Package

__all__ = "SBOM", "cyclonedx_to_json"


S = TypeVar("S", bound="SBOM")


def _add_maintenance_properties(component: Component, package: Package) -> None:
    """Add maintenance information as properties to a CycloneDX component.

    Args:
        component: CycloneDX component to add properties to
        package: Package with maintenance information

    """
    if package.maintenance_info:
        info = package.maintenance_info
        if info.repository_url:
            component.properties.add(
                Property(name="maintenance:repository_url", value=info.repository_url)
            )
        if info.last_commit_date:
            component.properties.add(
                Property(name="maintenance:last_commit_date", value=info.last_commit_date)
            )
        if info.days_since_update is not None:
            component.properties.add(
                Property(name="maintenance:days_since_update", value=str(info.days_since_update))
            )
        if info.is_stale:
            component.properties.add(Property(name="maintenance:is_stale", value="true"))
        if info.error:
            component.properties.add(Property(name="maintenance:error", value=info.error))


class SBOM:
    """Software Bill of Materials representation."""

    def __init__(
        self,
        dependencies: Iterable[tuple[Package, Package]] = (),
        root_packages: Iterable[Package] = (),
    ) -> None:
        """Initialize SBOM with dependencies and root packages."""
        self.dependencies: frozenset[tuple[Package, Package]] = frozenset(dependencies)
        self.root_packages: frozenset[Package] = frozenset(root_packages)

    @property
    def packages(self) -> frozenset[Package]:
        """Get all packages in the SBOM."""
        return self.root_packages | {p for deps in self.dependencies for p in deps}

    def __str__(self) -> str:
        """Return string representation of the SBOM."""
        return ", ".join(p.full_name for p in sorted(self.packages))

    def to_cyclonedx(self) -> Bom:
        """Convert SBOM to CycloneDX format."""
        bom = Bom()

        expanded: dict[Package, Component] = {}

        root_component: Component | None = None

        for root_package in sorted(self.root_packages, key=lambda package: package.full_name, reverse=True):
            root_component = Component(
                name=root_package.name,
                type=ComponentType.APPLICATION,
                version=str(root_package.version),
                bom_ref=root_package.full_name,
            )
            _add_maintenance_properties(root_component, root_package)
            bom.components.add(root_component)
            expanded[root_package] = root_component

        bom.metadata.tools.components.add(cdx_lib_component())
        bom.metadata.tools.components.add(
            Component(
                name="it-depends",
                supplier=OrganizationalEntity(name="Trail of Bits", urls=[XsUri("https://www.trailofbits.com/")]),
                type=ComponentType.APPLICATION,
                version=version,
            )
        )

        if root_component is not None:
            bom.metadata.component = root_component

        for pkg, depends_on in self.dependencies:
            if pkg not in expanded:
                component = Component(
                    name=pkg.name,
                    type=ComponentType.LIBRARY,
                    version=str(pkg.version),
                    bom_ref=f"{pkg.full_name}@{pkg.version!s}",
                )
                _add_maintenance_properties(component, pkg)
                bom.components.add(component)
                expanded[pkg] = component
            else:
                component = expanded[pkg]
            if depends_on not in expanded:
                d_component = Component(
                    name=depends_on.name,
                    type=ComponentType.LIBRARY,
                    version=str(depends_on.version),
                    bom_ref=f"{depends_on.full_name}@{depends_on.version!s}",
                )
                _add_maintenance_properties(d_component, depends_on)
                bom.components.add(d_component)
                expanded[depends_on] = d_component
            else:
                d_component = expanded[depends_on]
            bom.register_dependency(component, [d_component])

        return bom

    def __or__(self, other: "SBOM") -> "SBOM":
        """Combine two SBOMs."""
        return SBOM(self.dependencies | other.dependencies, self.root_packages | other.root_packages)

    def __hash__(self) -> int:
        """Return hash of the SBOM."""
        return hash((self.root_packages, self.dependencies))

    def __eq__(self, other: object) -> bool:
        """Check if two SBOMs are equal."""
        return (
            isinstance(other, SBOM)
            and self.root_packages == other.root_packages
            and self.dependencies == other.dependencies
        )


def cyclonedx_to_json(bom: Bom, indent: int = 2) -> str:
    """Convert CycloneDX BOM to JSON string."""
    return JsonV1Dot5(bom).output_as_string(indent=indent)
