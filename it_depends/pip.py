from pathlib import Path
from tempfile import mkdtemp, NamedTemporaryFile
import subprocess
import sys
from typing import Iterator, Optional

from johnnydep import JohnnyDist

from .dependencies import Dependency, DependencyClassifier, DependencyResolver, Package, SimpleSpec, Version


class PipResolver(DependencyResolver):
    def __init__(self, package_name_or_path: str):
        super().__init__()
        if (Path(package_name_or_path) / "setup.py").exists():
            self.path: Optional[str] = package_name_or_path
            self.package_name: Optional[str] = None
        else:
            self.path = None
            self.package_name = package_name_or_path
        self.tmp_dir: Optional[Path] = None
        self.wheel: Optional[Path] = None
        with self:
            dist = JohnnyDist(str(self.wheel))
            specifier = dist.specifier
            if specifier.startswith("=="):
                version = Version(specifier[2:])
            else:
                raise ValueError(f"Unexpected version specifier for {self.wheel.name}: {specifier!s}")
            package = Package(
                name=dist.name,
                version=version,
                dependencies=[
                    Dependency(package=child.name, semantic_version=SimpleSpec(child.specifier))
                    for child in dist.children
                ],
                source="pip"
            )
            self.add(package)

    def resolve_missing(self, dependency: Dependency) -> Iterator[Package]:
        dist = JohnnyDist(f"{dependency.package}{dependency.semantic_version}")
        print(dist.versions_available)
        package = Package(
            name=dist.name,
            version=Version(dist.specifier),
            dependencies=[
                Dependency(package=child.name, semantic_version=child.specifier) for child in dist.children
            ],
            source="pip"
        )
        self.add(package)
        yield package

    def open(self):
        if self.path is not None:
            tmp_file = NamedTemporaryFile("wb", prefix="wheel", suffix=".whl", delete=False)
            tmp_file.close()
            self.wheel = Path(tmp_file.name)
            self.tmp_dir = Path(mkdtemp())
            subprocess.check_call([
                sys.executable, "-m", "pip", "wheel", "--no-deps", "-w", str(self.tmp_dir), self.path
            ])
            found_wheel = False
            for whl in self.tmp_dir.glob("*.whl"):
                if found_wheel:
                    raise ValueError(f"`pip wheel --no-deps {self.path}` produced mutliple wheel files!")
                self.wheel = whl
                found_wheel = True
            if not found_wheel:
                raise ValueError(f"`pip wheel --no-deps {self.path}` did not produce a wheel file!")

    def close(self):
        if self.tmp_dir is not None:
            self.wheel.unlink()
            self.wheel = None
            self.tmp_dir.rmdir()
            self.tmp_dir = None


class PipClassifier(DependencyClassifier):
    name = "pip"
    description = "classifies the dependencies of Python packages using pip"

    def can_classify(self, path: str) -> bool:
        p = Path(path)
        return (p / "setup.py").exists() or (p / "requirements.txt").exists()

    def classify(self, path: str) -> DependencyResolver:
        return PipResolver(path)
