from abc import ABC, abstractmethod
from itertools import chain
import json
from typing import Dict, Iterable, Iterator, Optional, Union

from semantic_version import SimpleSpec, Version
from semantic_version.base import BaseSpec as SemanticVersion


class Dependency:
    def __init__(self, package: str, semantic_version: SemanticVersion = SimpleSpec("*")):
        self.package: str = package
        self.semantic_version: SemanticVersion = semantic_version


class Package:
    def __init__(
            self,
            name: str,
            version: Version,
            dependencies: Iterable[Dependency] = (),
            source: Optional[str] = None
    ):
        self.name: str = name
        self.version: Version = version
        self.dependencies: Dict[str, Dependency] = {
            dep.package: dep for dep in dependencies
        }
        self.source: Optional[str] = source

    def to_obj(self) -> Dict[str, Union[str, Dict[str, str]]]:
        ret = {
            "name": self.name,
            "version": str(self.version),
            "dependencies": {
                package: str(dep.semantic_version) for package, dep in self.dependencies.items()
            }
        }
        if self.source is not None:
            ret["source"] = self.source
        return ret

    def dumps(self) -> str:
        return json.dumps(self.to_obj())

    def __eq__(self, other):
        return isinstance(other, Package) and self.name == other.name and self.version == other.version

    def __ne__(self, other):
        return not (self == other)

    def __hash__(self):
        return hash((self.name, self.version))


class DependencyResolver:
    def __init__(self, packages: Iterable[Package] = ()):
        self.packages: Dict[str, Dict[Version, Package]] = {}
        for package in packages:
            self.add(package)
        self._entries: int = 0

    def open(self):
        pass

    def close(self):
        pass

    def __enter__(self) -> "DependencyResolver":
        self._entries += 1
        if self._entries == 1:
            self.open()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self._entries -= 1
        if self._entries == 0:
            self.close()

    def __len__(self):
        return sum(map(len, self.packages.values()))

    def __iter__(self) -> Iterator[Package]:
        return chain(*(p.values() for p in self.packages.values()))

    def add(self, package: Package):
        self.packages.setdefault(package.name, {})[package.version] = package

    def resolve_missing(self, dependency: Dependency) -> Iterator[Package]:
        return iter(())

    def resolve(self, dependency: Dependency) -> Iterator[Package]:
        yielded = False
        for version, package in self.packages.get(dependency.package, default={}).items():
            if version in dependency.semantic_version:
                yield package
                yielded = True
        if not yielded:
            yield from self.resolve_missing(dependency)


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
    def classify(self, path: str) -> DependencyResolver:
        raise NotImplementedError()
