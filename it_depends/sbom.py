from spdx.creationinfo import CreationInfo, Creator
from spdx.document import Document, License
from spdx.package import Package as SPDXPackage
#from spdx.relationship import Relationship
from spdx.version import Version
from spdx.writers import json

from .dependencies import PackageCache, Package

__all__ = "package_to_spdx",


def package_to_spdx(package: Package, packages: PackageCache) -> str:
    spdx_package = SPDXPackage(
        name=package.name,
        spdx_id=package.full_name,
    )
    spdx_package.source_info = package.source
    document = Document(
        version=Version(2, 1),
        data_license=License.from_identifier("CC0-1.0"),
        name=f"It-Depends Dependencies for {package.name}",
        namespace=f"It-Depends {package.full_name}",
        spdx_id=f"SBOM:{package.full_name}:SPDXRef-DOCUMENT",
        package=spdx_package,
    )
    document.creation_info = CreationInfo()
    document.creation_info.add_creator(Creator("https://github.com/trailofbits/it-depends"))
    document.creation_info.set_created_now()
    import sys
    json.write_document(document, sys.stdout)
    return ""
