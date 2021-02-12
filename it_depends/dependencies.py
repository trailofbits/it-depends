from abc import ABC, abstractmethod
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


CLASSIFIERS_BY_NAME: Dict[str, "DependencyClassifier"] = {}


class ClassifierAvailability:
    def __init__(self, is_available: bool, reason: str = ""):
        if not is_available and not reason:
            raise ValueError("You must provide a reason if `not is_available`")
        self.is_available: bool = is_available
        self.reason: str = reason

    def __bool__(self):
        return self.is_available


class DependencyClassifier(ABC):
    name: str
    description: str

    def __init_subclass__(cls, **kwargs):
        if not hasattr(cls, "name") or cls.name is None:
            raise TypeError(f"{cls.__name__} must define a `name` class member")
        elif not hasattr(cls, "description") or cls.description is None:
            raise TypeError(f"{cls.__name__} must define a `description` class member")
        CLASSIFIERS_BY_NAME[cls.name] = cls()

    def is_available(self) -> ClassifierAvailability:
        return ClassifierAvailability(True)

    @abstractmethod
    def can_classify(self, path: str) -> bool:
        raise NotImplementedError()

    @abstractmethod
    def classify(self, path: str) -> List[Package]:
        raise NotImplementedError()
