from pathlib import Path
from typing import Dict, FrozenSet, Iterable, Iterator, Optional, Tuple, Union

from semantic_version import SimpleSpec, Version
from sqlalchemy import Column, create_engine, distinct, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.ext.hybrid import hybrid_property
from sqlalchemy.orm import relationship, sessionmaker

from .dependencies import CLASSIFIERS_BY_NAME, Dependency, DependencyClassifier, Package, SemanticVersion, PackageCache

DEFAULT_DB_PATH = Path.home() / ".config" / "it-depends" / "dependencies.sqlite"

Base = declarative_base()


class Resolution(Base):
    __tablename__ = "resolutions"

    id = Column(Integer, primary_key=True)
    package = Column(String, nullable=False)
    version = Column(String, nullable=True)
    source = Column(String, nullable=True)

    __table_args__ = (
        UniqueConstraint("package", "version", "source", name="resolution_unique_constraint"),
    )


class DBDependency(Base, Dependency):
    __tablename__ = "dependencies"

    id = Column(Integer, primary_key=True)
    from_package_id = Column(Integer, ForeignKey("packages.id"))
    from_package = relationship("DBPackage", back_populates="raw_dependencies")
    package = Column(String, nullable=False)
    semantic_version_string = Column("semantic_version", String, nullable=True)

    __table_args__ = (
        UniqueConstraint("from_package_id", "package", "semantic_version", name="dependency_unique_constraint"),
    )

    def __init__(self, package: "DBPackage", dep: Dependency):
        # We intentionally skip calling super().__init__()
        self.from_package_id = package.id
        self.package = dep.package
        self.semantic_version = dep.semantic_version

    @hybrid_property
    def semantic_version(self) -> SemanticVersion:
        try:
            classifier = CLASSIFIERS_BY_NAME[self.from_package.source]
        except KeyError:
            classifier = DependencyClassifier
        return classifier.parse_spec(self.semantic_version_string)

    @semantic_version.setter
    def semantic_version(self, new_version: Union[SemanticVersion, str]):
        self.semantic_version_string = str(new_version)


class DependencyMapping:
    def __init__(self, package: "DBPackage"):
        super().__init__()
        self._deps: Dict[str, Dependency] = {
            dep.package: Dependency(dep.package, dep.semantic_version) for dep in package.raw_dependencies
        }

    def items(self) -> Iterator[Tuple[str, Dependency]]:
        yield from self._deps.items()

    def keys(self) -> Iterable[str]:
        return self._deps.keys()

    def values(self) -> Iterable[Dependency]:
        return self._deps.values()

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


class DBPackage(Base, Package):
    __tablename__ = "packages"

    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    version_str = Column("version", String, nullable=False)
    source = Column(String, nullable=True)

    __table_args__ = (
        UniqueConstraint("name", "version", "source", name="package_unique_constraint"),
    )

    raw_dependencies = relationship("DBDependency", back_populates="from_package", cascade="all, delete, delete-orphan")

    def __init__(self, package: Package):
        # We intentionally skip calling super().__init__()
        self.name = package.name
        self.version = package.version
        self.source = package.source

    @staticmethod
    def from_package(package: Package, session) -> "DBPackage":
        if not isinstance(package, DBPackage):
            dep_pkg = package
            package = DBPackage(package)
            session.add(package)
            session.flush()
            session.add_all([DBDependency(package, dep) for dep in dep_pkg.dependencies.values()])
        else:
            session.add(package)
        return package

    def to_package(self) -> Package:
        return Package(name=self.name, version=self.version, dependencies=(
            Dependency(package=dep.package, semantic_version=dep.semantic_version) for dep in self.raw_dependencies
        ), source=self.source)

    @property
    def version(self) -> Version:
        return Version.coerce(self.version_str)

    @version.setter
    def version(self, new_version: Union[Version, str]):
        self.version_str = str(new_version)

    @property
    def dependencies(self) -> DependencyMapping:
        return DependencyMapping(self)


class SourceFilteredPackageCache(PackageCache):
    def __init__(self, source: str, parent: "DBPackageCache"):
        super().__init__()
        self.source: str = source
        self.parent: DBPackageCache = parent

    def __len__(self):
        return self.parent.session.query(DBPackage).filter(DBPackage.source.like(self.source)).count()

    def __iter__(self) -> Iterator[Package]:
        yield from [p.to_package()
                    for p in self.parent.session.query(DBPackage).filter(DBPackage.source.like(self.source)).all()]

    def was_resolved(self, dependency: Dependency, source: Optional[str] = None) -> bool:
        if source is not None and source != self.source:
            return False
        else:
            return self.parent.was_resolved(dependency, source=self.source)

    def set_resolved(self, dependency: Dependency, source: Optional[str]):
        self.parent.set_resolved(dependency, source=source)

    def from_source(self, source: Optional[str]) -> "PackageCache":
        return SourceFilteredPackageCache(source, self.parent)

    def package_versions(self, package_name: str) -> Iterator[Package]:
        yield from [p.to_package() for p in self.parent.session.query(DBPackage).filter(
            DBPackage.name.like(package_name), DBPackage.source.like(self.source)
        ).all()]

    def package_names(self) -> FrozenSet[str]:
        return frozenset(self.parent.session.query(distinct(DBPackage.name))
                         .filter(DBPackage.source.like(self.source)).all())

    def match(self, to_match: Union[str, Package, DBDependency]) -> Iterator[Package]:
        return self.parent.match(to_match, source=self.source)

    def add(self, package: Package, source: Optional[DependencyClassifier] = None):
        return self.parent.add(package, source)


class DBPackageCache(PackageCache):
    def __init__(self, db: Union[str, Path] = ":memory:"):
        super().__init__()
        if db == ":memory:":
            db = "sqlite:///:memory:"
        elif db == "sqlite:///:memory:":
            pass
        elif isinstance(db, str):
            if db.startswith("sqlite:///"):
                db = db[len("sqlite:///"):]
            db = Path(db)
        if isinstance(db, Path):
            db.parent.mkdir(parents=True, exist_ok=True)
            db = f"sqlite:///{db.absolute()!s}?check_same_thread=False"
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

    def add(self, package: Package, source: Optional[DependencyClassifier] = None):
        self.extend((package,), source=source)

    def extend(self, packages: Iterable[Package], source: Optional[DependencyClassifier] = None):
        for package in packages:
            for existing in self.match(package):
                if len(existing.dependencies) > len(package.dependencies):
                    raise ValueError(f"Package {package!s} has already been resolved with more dependencies")
                elif existing.dependencies != package.dependencies:
                    existing.dependencies = package.dependencies
                    self.session.commit()
                found_existing = True
                break
            else:
                found_existing = False
            if found_existing:
                continue
            if package.source is None and source is not None:
                package.source = source.name
            if isinstance(package, DBPackage):
                self.session.add(package)
            else:
                _ = DBPackage.from_package(package, self.session)
        self.session.commit()

    def __len__(self):
        return self.session.query(DBPackage).count()

    def __iter__(self) -> Iterator[Package]:
        yield from self.session.query(DBPackage).all()

    def from_source(self, source: Optional[str]) -> SourceFilteredPackageCache:
        return SourceFilteredPackageCache(source, self)

    def package_versions(self, package_name: str) -> Iterator[Package]:
        yield from [
            p.to_package() for p in self.session.query(DBPackage).filter(DBPackage.name.like(package_name)).all()
        ]

    def package_names(self) -> FrozenSet[str]:
        return frozenset(result[0] for result in self.session.query(distinct(DBPackage.name)).all())

    def _make_query(self, to_match: Union[str, Package], source: Optional[str] = None):
        if source is not None:
            filters = (DBPackage.source.like(source),)
        else:
            filters = ()
        if isinstance(to_match, Package):
            return self.session.query(DBPackage).filter(
                DBPackage.name.like(to_match.name), DBPackage.version_str.like(str(to_match.version)), *filters
            )
        else:
            return self.session.query(DBPackage).filter(DBPackage.name.like(to_match), *filters)

    def match(
            self, to_match: Union[str, Package, Dependency], source: Optional[str] = None
    ) -> Iterator[Package]:
        if isinstance(to_match, Dependency):
            for package in self.match(to_match.package, source=source):
                if to_match.semantic_version is not None and package.version in to_match.semantic_version:
                    yield package
        else:
            # we intentionally build a list before yielding so that we don't keep the session query lingering
            yield from [package.to_package() for package in self._make_query(to_match, source=source).all()]

    def was_resolved(self, dependency: Dependency, source: Optional[str] = None) -> bool:
        if source is not None:
            filters = (Resolution.source.like(source),)
        else:
            filters = ()
        return self.session.query(Resolution).filter(
            Resolution.package.like(dependency.package),
            Resolution.version == str(dependency.semantic_version),
            *filters
        ).limit(1).count() > 0

    def set_resolved(self, dependency: Dependency, source: Optional[str]):
        self.session.add(
            Resolution(package=dependency.package, version=str(dependency.semantic_version), source=source)
        )
        self.session.commit()
