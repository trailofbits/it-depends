import json
from pathlib import Path
import shutil
import subprocess
from typing import Dict, Iterator, List

from .dependencies import ClassifierAvailability, Dependency, DependencyClassifier, Package


class NPMPackage:
    def __init__(self, name: str, version: str):
        self.name: str = name
        self.version: str = version

    def dependencies(self) -> Iterator["NPMPackage"]:
        try:
            output = subprocess.check_output(["npm", "view", "--json", f"{self.name}@{self.version}", "dependencies"])
        except subprocess.CalledProcessError as e:
            raise ValueError(f"Error running `npm view --json {self.name}@{self.version} dependencies`: {e!s}")
        try:
            deps = json.loads(output)
        except ValueError as e:
            raise ValueError(
                f"Error parsing output of `npm view --json {self.name}@{self.version} dependencies`: {e!s}"
            )
        if isinstance(deps, list):
            # this means that there are multiple dependencies that match the version
            in_data = False
            versions = []
            for line in subprocess.check_output(
                    ["npm", "view", f"{self.name}@{self.version}", "dependencies"]
            ).splitlines():
                line = line.decode("utf-8").strip()
                if in_data:
                    if line.endswith("}"):
                        in_data = False
                    continue
                elif line.startswith("{"):
                    in_data = True
                else:
                    versions.append(line)
            for pkg_version, dep_dict in zip(versions, deps):
                for dep, version in dep_dict.items():
                    yield NPMPackage(dep, version)
        else:
            for dep, version in deps.items():
                yield NPMPackage(dep, version)

    def packages(self) -> List[Package]:
        queue: List[NPMPackage] = [self]
        packages_by_name: Dict[str, Package] = {}
        while queue:
            pkg = queue.pop()
            if pkg.name in packages_by_name:
                continue
            new_deps = list(pkg.dependencies())
            packages_by_name[pkg.name] = Package(pkg.name, pkg.version, (
                Dependency(dep.name, dep.version, False) for dep in new_deps
            ))
            queue.extend((d for d in new_deps if d not in packages_by_name))
        return list(packages_by_name.values())


class LocalNPMPackage(NPMPackage):
    def __init__(self, package_json_path: str):
        self.path: Path = Path(package_json_path)
        if self.path.is_dir():
            self.path = self.path / "package.json"
        if not self.path.exists():
            raise ValueError(f"Expected a package.json file at {self.path!s}")
        with open(self.path, "r") as json_file:
            package = json.load(json_file)
        if "name" not in package:
            raise ValueError(f"Expected \"name\" key in {self.path!s}")
        if "dependencies" in package:
            self._dependencies: Dict[str, str] = package["dependencies"]
        else:
            self._dependencies = {}
        if "version" in package:
            super().__init__(package["name"], package["version"])
        else:
            super().__init__(package["name"], "")

    def dependencies(self) -> Iterator[NPMPackage]:
        for dep_name, version in self._dependencies.items():
            yield NPMPackage(dep_name, version)


class NPMClassifier(DependencyClassifier):
    name = "npm"
    description = "classifies the dependencies of JavaScript packages using `npm`"

    def is_available(self) -> ClassifierAvailability:
        if shutil.which("npm") is None:
            return ClassifierAvailability(False, "`npm` does not appear to be installed! "
                                                 "Make sure it is installed and in the PATH.")
        return ClassifierAvailability(True)

    def can_classify(self, path: str) -> bool:
        return (Path(path) / "package.json").exists()

    def classify(self, path: str) -> List[Package]:
        return LocalNPMPackage(path).packages()
