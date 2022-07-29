from pathlib import Path
from typing import Any, Dict, FrozenSet, Iterable, Iterator, Optional, Tuple, Union

from semantic_version import Version
from sqlalchemy import (
    Column,
    create_engine,
    distinct,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.ext.hybrid import hybrid_property
from sqlalchemy.orm import relationship, sessionmaker

from .dependencies import (
    resolver_by_name,
    Dependency,
    DependencyResolver,
    Package,
    SemanticVersion,
    PackageCache,
)
from .it_depends import APP_DIRS

DEFAULT_DB_PATH = Path(APP_DIRS.user_cache_dir) / "dependencies.sqlite"

Base = declarative_base()


class Resolution(Base):  # type: ignore
    __tablename__ = "resolutions"

    id = Column(Integer, primary_key=True)
    package = Column(String, nullable=False)
    version = Column(String, nullable=True)
    source = Column(String, nullable=True)

    __table_args__ = (
        UniqueConstraint("package", "version", "source", name="resolution_unique_constraint"),
    )


class Updated(Base):  # type: ignore
    __tablename__ = "updated"

    id = Column(Integer, primary_key=True)
    package = Column(String, nullable=False)
    version = Column(String, nullable=True)
    source = Column(String, nullable=True)
    resolver = Column(String, nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "package", "version", "source", "resolver", name="updated_unique_constraint"
        ),
    )


class DBDependency(Base, Dependency):  # type: ignore
    __tablename__ = "dependencies"

    id = Column(Integer, primary_key=True)
    from_package_id = Column(Integer, ForeignKey("packages.id"))
    from_package = relationship("DBPackage", back_populates="raw_dependencies")
    source = Column(String, nullable=False)
    package = Column(String, nullable=False)
    semantic_version_string = Column("semantic_version", String, nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "from_package_id",
            "package",
            "semantic_version",
            name="dependency_unique_constraint",
        ),
    )

    def __init__(self, package: "DBPackage", dep: Dependency):
        # We intentionally skip calling super().__init__()
        self.from_package_id = package.id
        self.source = dep.source
        self.package = dep.package
        self.semantic_version = dep.semantic_version  # type: ignore

    @hybrid_property  # type: ignore
    def semantic_version(self) -> SemanticVersion:
        resolver = resolver_by_name(self.source)
        return resolver.parse_spec(self.semantic_version_string)

    @semantic_version.setter  # type: ignore
    def semantic_version(self, new_version: Union[SemanticVersion, str]):
        self.semantic_version_string = str(new_version)


class DependencyMapping:
    def __init__(self, package: "DBPackage"):
        super().__init__()
        self._deps: Dict[str, Dependency] = {
            dep.package: Dependency(
                package=dep.package,
                source=dep.source,
                semantic_version=dep.semantic_version,
            )
            for dep in package.raw_dependencies
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


class DBPackage(Base, Package):  # type: ignore
    __tablename__ = "packages"

    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    version_str = Column("version", String, nullable=False)
    source = Column("source", String, nullable=False)

    __table_args__ = (
        UniqueConstraint("name", "version", "source", name="package_unique_constraint"),
    )

    raw_dependencies = relationship(
        "DBDependency",
        back_populates="from_package",
        cascade="all, delete, delete-orphan",
    )

    def __init__(self, package: Package):
        # We intentionally skip calling super().__init__()
        self.name = package.name
        self.version = package.version
        self.source = package.source

    @property
    def resolver(self) -> DependencyResolver:
        return resolver_by_name(self.source)

    @staticmethod
    def from_package(package: Package, session) -> "DBPackage":
        if not isinstance(package, DBPackage):
            dep_pkg = package
            package = DBPackage(package)
            session.add(package)
            session.flush()
            session.add_all([DBDependency(package, dep) for dep in dep_pkg.dependencies])
        else:
            session.add(package)
        return package

    def to_package(self) -> Package:
        return Package(
            source=self.source,
            name=self.name,
            version=self.version,
            dependencies=(
                Dependency(
                    package=dep.package,
                    semantic_version=dep.semantic_version,
                    source=dep.source,
                )
                for dep in self.raw_dependencies
            ),
        )

    @property
    def version(self) -> Version:
        return self.resolver.parse_version(self.version_str)

    @version.setter
    def version(self, new_version: Union[Version, str]):
        self.version_str = str(new_version)

    @property
    def dependencies(self) -> DependencyMapping:  # type: ignore
        return DependencyMapping(self)


class SourceFilteredPackageCache(PackageCache):
    def __init__(self, source: Optional[str], parent: "DBPackageCache"):
        super().__init__()
        self.source: Optional[str] = source
        self.parent: DBPackageCache = parent

    def __len__(self):
        return (
            self.parent.session.query(DBPackage)
            .filter(DBPackage.source_name.like(self.source))
            .count()
        )

    def __iter__(self) -> Iterator[Package]:
        yield from [
            p.to_package()
            for p in self.parent.session.query(DBPackage)
            .filter(DBPackage.source_name.like(self.source))
            .all()
        ]

    def was_resolved(self, dependency: Dependency) -> bool:
        return self.parent.was_resolved(dependency)

    def set_resolved(self, dependency: Dependency):
        self.parent.set_resolved(dependency)

    def from_source(self, source: Optional[str]) -> "PackageCache":
        return SourceFilteredPackageCache(source, self.parent)

    def package_versions(self, package_name: str) -> Iterator[Package]:
        yield from [
            p.to_package()
            for p in self.parent.session.query(DBPackage)
            .filter(
                DBPackage.name.like(package_name),
                DBPackage.source_name.like(self.source),
            )
            .all()
        ]

    def package_full_names(self) -> FrozenSet[str]:
        return frozenset(
            self.parent.session.query(distinct(DBPackage.name))
            .filter(DBPackage.source_name.like(self.source))
            .all()
        )

    def match(self, to_match: Union[str, Package, Dependency]) -> Iterator[Package]:
        return self.parent.match(to_match)

    def add(self, package: Package):
        return self.parent.add(package)

    def set_updated(self, package: Package, resolver: str):
        return self.parent.set_updated(package, resolver)

    def was_updated(self, package: Package, resolver: str) -> bool:
        return self.parent.was_updated(package, resolver)

    def updated_by(self, package: Package) -> FrozenSet[str]:
        return self.parent.updated_by(package)


class DBPackageCache(PackageCache):
    def __init__(self, db: Union[str, Path] = ":memory:"):
        super().__init__()
        if db == ":memory:":
            db = "sqlite:///:memory:"
        elif db == "sqlite:///:memory:":
            pass
        elif isinstance(db, str):
            if db.startswith("sqlite:///"):
                db = db[len("sqlite:///") :]
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

    def add(self, package: Package):
        self.extend((package,))

    def extend(self, packages: Iterable[Package]):
        for package in packages:
            for existing in self.match(package):
                if len(existing.dependencies) > len(package.dependencies):
                    raise ValueError(
                        f"Package {package!s} has already been resolved with more dependencies: "
                        f"{existing!s}"
                    )
                elif existing.dependencies != package.dependencies:
                    existing.dependencies = package.dependencies
                    self.session.commit()
                found_existing = True
                break
            else:
                found_existing = False
            if found_existing:
                continue
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

    def package_versions(self, package_full_name: str) -> Iterator[Package]:
        yield from [
            p.to_package()
            for p in self.session.query(DBPackage)
            .filter(DBPackage.name.like(package_full_name))
            .all()
        ]

    def package_full_names(self) -> FrozenSet[str]:
        return frozenset(
            f"{result[0]}:{result[1]}"
            for result in self.session.query(
                distinct(DBPackage.source), distinct(DBPackage.name)
            ).all()
        )

    def _make_query(self, to_match: Union[str, Package], source: Optional[str] = None):
        if source is None and isinstance(to_match, Package):
            source = to_match.source
        if source is not None:
            filters: Tuple[Any, ...] = (DBPackage.source.like(source),)
        else:
            filters = ()
        if isinstance(to_match, Package):
            return self.session.query(DBPackage).filter(
                DBPackage.name.like(to_match.name),
                DBPackage.version_str.like(str(to_match.version)),
                *filters,
            )
        else:
            return self.session.query(DBPackage).filter(DBPackage.name.like(to_match), *filters)

    def match(self, to_match: Union[str, Package, Dependency]) -> Iterator[Package]:
        if isinstance(to_match, Dependency):
            for package in self._make_query(to_match.package, source=to_match.source):
                if package.version in to_match.semantic_version:
                    yield package.to_package()
        else:
            if isinstance(to_match, Package):
                source: Optional[str] = to_match.source
            else:
                source = None
            # we intentionally build a list before yielding so that we don't keep the session query lingering
            yield from [
                package.to_package() for package in self._make_query(to_match, source=source).all()
            ]

    def was_resolved(self, dependency: Dependency) -> bool:
        return (
            self.session.query(Resolution)
            .filter(
                Resolution.package.like(dependency.package),
                Resolution.version == str(dependency.semantic_version),
                Resolution.source.like(dependency.source),
            )
            .limit(1)
            .count()
            > 0
        )

    def set_resolved(self, dependency: Dependency):
        if self.was_resolved(dependency):
            return
        self.session.add(
            Resolution(
                package=dependency.package,
                version=str(dependency.semantic_version),
                source=dependency.source,
            )
        )
        self.session.commit()

    def updated_by(self, package: Package) -> FrozenSet[str]:
        return frozenset(
            u.resolver
            for u in self.session.query(Updated).filter(
                Updated.source.like(package.source),
                Updated.package.like(package.name),
                Updated.version == str(package.version),
            )
        )

    def was_updated(self, package: Package, resolver: str) -> bool:
        if package.source == resolver:
            return True
        return (
            self.session.query(Updated)
            .filter(
                Updated.source.like(package.source),
                Updated.package.like(package.name),
                Updated.version.like(str(package.version)),
                Updated.resolver.like(resolver),
            )
            .limit(1)
            .count()
            > 0
        )

    def set_updated(self, package: Package, resolver: str):
        if self.was_updated(package, resolver):
            return
        self.session.add(
            Updated(
                package=package.name,
                version=str(package.version),
                source=package.source,
                resolver=resolver,
            )
        )
        self.session.commit()
