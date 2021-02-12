import json
from os import chdir, getcwd
from pathlib import Path
import shutil
import subprocess
from typing import Iterable, List

from .dependencies import ClassifierAvailability, Dependency, DependencyClassifier, Package


def get_dependencies(cargo_package_path: str, check_for_cargo: bool = True) -> Iterable[Package]:
    if check_for_cargo and shutil.which("cargo") is None:
        raise ValueError("`cargo` does not appear to be installed! Make sure it is installed and in the PATH.")

    orig_dir = getcwd()
    chdir(cargo_package_path)

    try:
        metadata = json.loads(subprocess.check_output(["cargo", "metadata", "--format-version", "1"]))
    finally:
        chdir(orig_dir)

    for package in metadata["packages"]:
        yield Package(
            name=package["name"],
            version=package["version"],
            dependencies=[
                Dependency(
                    package=dep["name"],
                    version=dep["req"]
                )
                for dep in package["dependencies"]
            ]
        )


class PipClassifier(DependencyClassifier):
    name = "cargo"
    description = "classifies the dependencies of Rust packages using `cargo metadata`"

    def is_available(self) -> ClassifierAvailability:
        if shutil.which("cargo") is None:
            return ClassifierAvailability(False, "`cargo` does not appear to be installed! "
                                                 "Make sure it is installed and in the PATH.")
        return ClassifierAvailability(True)

    def can_classify(self, path: str) -> bool:
        return (Path(path) / "Cargo.toml").exists()

    def classify(self, path: str) -> List[Package]:
        return get_dependencies(path, check_for_cargo=False)
