from typing import Dict, Iterable, List, Optional, Tuple

from cyclonedx.builder.this import this_component as cdx_lib_component
from cyclonedx.model import XsUri
from cyclonedx.model.bom import Bom
from cyclonedx.model.component import Component, ComponentType
from cyclonedx.model.contact import OrganizationalEntity
from cyclonedx.output.json import JsonV1Dot5

from . import version
from .dependencies import PackageCache, Package

__all__ = "package_to_cyclonedx", "cyclonedx_to_json"


def package_to_cyclonedx(
        package: Package, packages: PackageCache, bom: Optional[Bom] = None, only_latest: bool = False
) -> Bom:
    root_component = Component(
        name=package.name,
        type=ComponentType.APPLICATION,
        bom_ref=package.full_name,
    )
    if bom is None:
        bom = Bom()
        bom.metadata.tools.components.add(cdx_lib_component())
        bom.metadata.tools.components.add(Component(
            name="it-depends",
            supplier=OrganizationalEntity(
                name="Trail of Bits",
                urls=[XsUri("https://www.trailofbits.com/")]
            ),
            type=ComponentType.APPLICATION,
            version=version(),
        ))

        bom.metadata.component = root_component

    package_queue: List[Tuple[Optional[Component], Package]] = [(None, package)]

    expanded: Dict[Package, Component] = {}

    while package_queue:
        parent_component, pkg = package_queue.pop()

        if pkg in expanded:
            if parent_component is not None:
                bom.register_dependency(parent_component, [expanded[pkg]])
            continue

        component = Component(
            name=pkg.name,
            type=ComponentType.LIBRARY,
            version=str(pkg.version),
            bom_ref=f"{pkg.full_name}@{pkg.version!s}"
        )

        expanded[pkg] = component

        bom.components.add(component)
        if parent_component is not None:
            bom.register_dependency(parent_component, [component])

        for dep in pkg.dependencies:
            if only_latest:
                latest = packages.latest_match(dep)
                if latest is None:
                    continue
                matches: Iterable[Package] = (latest,)
            else:
                matches = packages.match(dep)
            for resolved in matches:
                if resolved in expanded:
                    bom.register_dependency(component, [expanded[resolved]])
                else:
                    package_queue.append((component, resolved))

    return bom


def cyclonedx_to_json(bom: Bom, indent: int = 2) -> str:
    return JsonV1Dot5(bom).output_as_string(indent=indent)
