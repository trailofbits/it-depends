import json
from typing import Dict, Iterable, List, Optional, Union


class Dependency:
    def __init__(self, package: str, version: Optional[str] = None, locked: bool = False):
        self.package: str = package
        self.version: Optional[str] = version
        self.locked: bool = locked

    def to_obj(self) -> Dict[str, Union[Optional[str], bool]]:
        return {
            "package": self.package,
            "version": self.version,
            "locked": self.locked
        }

    def dumps(self) -> str:
        return json.dumps(self.to_obj())

    @staticmethod
    def load(serialized: Union[str, Dict[str, Union[Optional[str], bool]]]) -> "Dependency":
        if isinstance(serialized, str):
            serialized = json.loads(serialized)
        return Dependency(package=serialized["package"], version=serialized["version"], locked=serialized["locked"])


class Package:
    def __init__(self, name: str, version: Optional[str] = None, dependencies: Iterable[Dependency] = ()):
        self.name: str = name
        self.version: Optional[str] = version
        self.dependencies: List[Dependency] = list(dependencies)

    def to_obj(self) -> Dict[str, Union[Optional[str], bool]]:
        return {
            "package": self.name,
            "version": self.version,
            "dependencies": [d.to_obj() for d in self.dependencies]
        }

    def dumps(self) -> str:
        return json.dumps(self.to_obj())

    @staticmethod
    def load(serialized: Union[str, Dict[str, Union[Optional[str], bool]]]) -> "Package":
        if isinstance(serialized, str):
            serialized = json.loads(serialized)
        return Package(
            name=serialized["package"],
            version=serialized["version"],
            dependencies=[Dependency.load(d) for d in serialized["dependencies"]]
        )
