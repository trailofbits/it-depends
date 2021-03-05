from typing import Dict, Union, Iterable, Iterator, Optional, FrozenSet

from semantic_version import SimpleSpec, Version
from sqlalchemy import Column, create_engine, distinct, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.ext.hybrid import hybrid_property
from sqlalchemy.orm import relationship, sessionmaker

from .dependencies import (
    Dependency as DepDependency, DependencyClassifier, Package as DepPackage, SemanticVersion, PackageCache
)

Base = declarative_base()


class Dependency(Base, DepDependency):
    __tablename__ = "dependencies"

    id = Column(Integer, primary_key=True)
    from_package_id = Column(Integer, ForeignKey("packages.id"))
    from_package = relationship("Package", back_populates="raw_dependencies")
    package = Column(String, nullable=False)
    _version = Column("version", String, nullable=True)

    __table_args__ = (
        UniqueConstraint("from_package_id", "package", "version", name="dependency_unique_constraint"),
    )

    def __init__(self, package: "Package", dep: DepDependency):
        self.from_package_id = package.id
        self.package = dep.package
        self.version = dep.semantic_version

    @hybrid_property
    def version(self) -> SemanticVersion:
        return SimpleSpec.parse(self._version)

    @version.setter
    def version(self, new_version: Union[SemanticVersion, str]):
        self._version = str(new_version)


class DependencyMapping(Dict[str, Dependency]):
    def __init__(self, package: "Package"):
        super().__init__()
        self._package: Package = package
        self._deps: Dict[str, Dependency] = {
            dep.package: dep for dep in self._package.raw_dependencies
        }

    def __setitem__(self, dep_name: str, dep: Dependency):
        self._deps[dep_name] = dep

    def __delitem__(self, dep_name: str):
        pass

    def __getitem__(self, package_name: str) -> Dependency:
        return self._deps[package_name]

    def __len__(self) -> int:
        return len(self._deps)

    def __iter__(self) -> Iterator[str]:
        return iter(self._deps)


class Package(Base, DepPackage):
    __tablename__ = "packages"

    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    version_str = Column("version", String, nullable=False)
    source = Column(String, nullable=True)

    __table_args__ = (
        UniqueConstraint("name", "version", "source", name="package_unique_constraint"),
    )

    raw_dependencies = relationship("Dependency", back_populates="from_package", cascade="all, delete, delete-orphan")

    def __init__(self, package: DepPackage):
        self.name = package.name
        self.version = package.version
        self.source = package.source

    @staticmethod
    def from_package(package: DepPackage, session) -> "Package":
        if not isinstance(package, Package):
            dep_pkg = package
            package = Package(package)
            session.add(package)
            session.flush()
            session.add_all([Dependency(package, dep) for dep in dep_pkg.dependencies.values()])
        else:
            session.add(package)
        return package

    @property
    def version(self) -> Version:
        return Version.coerce(self.version_str)

    @version.setter
    def version(self, new_version: Union[Version, str]):
        self.version_str = str(new_version)

    @property
    def dependencies(self) -> DependencyMapping:
        return DependencyMapping(self)


class DBPackageCache(PackageCache):
    def __init__(self, db: str = ":memory:"):
        super().__init__()
        if isinstance(db, str) and not db.startswith("sqlite:///"):
            db = f"sqlite:///{db}"
        self.db: str = db
        self._session = None

    def open(self):
        if isinstance(self.db, str):
            db = create_engine(self.db)
        else:
            db = self.db
        self._session = sessionmaker(bind=db)()
        Base.metadata.create_all(db)

    def close(self):
        self._session = None

    @property
    def session(self):
        return self._session

    def add(self, package: DepPackage, source: Optional[DependencyClassifier] = None):
        self.extend((package,), source=source)

    def extend(self, packages: Iterable[DepPackage], source: Optional[DependencyClassifier] = None):
        for package in packages:
            for existing in self.match(package):
                if len(existing.dependencies) > len(package.dependencies):
                    raise ValueError(f"Package {package!s} has already been resolved with more dependencies")
                found_existing = True
                break
            else:
                found_existing = False
            if found_existing:
                continue
            if package.source is None and source is not None:
                package.source = source.name
            if isinstance(package, Package):
                self.session.add(package)
            else:
                _ = Package.from_package(package, self.session)
        self.session.commit()

    def __len__(self):
        return self.session.query(Package).count()

    def __iter__(self) -> Iterator[Package]:
        return self.session.query(Package).all()

    def from_source(self, source: Optional[str]) -> "PackageCache":
        raise NotImplementedError()

    def package_versions(self, package_name: str) -> Iterator[Package]:
        return self.session.query(Package).filter(Package.name.like(package_name)).all()

    def package_names(self) -> FrozenSet[str]:
        return frozenset(self.session.query(distinct(Package.name)).all())

    def match(self, to_match: Union[str, DepPackage, DepDependency]) -> Iterator[Package]:
        if isinstance(to_match, DepPackage):
            yield from self.session.query(Package).filter(
                Package.name.like(to_match.name), Package.version_str.like(str(to_match.version))
            ).all()
        elif isinstance(to_match, DepDependency):
            for package in self.match(to_match.package):
                if package.version in to_match.semantic_version:
                    yield package
        else:
            yield from self.session.query(Package).filter(Package.name.like(to_match)).all()
