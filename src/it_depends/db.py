"""Database models and package cache implementations."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator

if TYPE_CHECKING:
    from semantic_version import Version
from sqlalchemy import (
    Column,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    create_engine,
    distinct,
)
from sqlalchemy.orm import DeclarativeBase, relationship, sessionmaker

from .dependencies import (
    Dependency,
    Package,
    PackageCache,
    resolver_by_name,
)

if TYPE_CHECKING:
    from .dependencies import DependencyResolver
from .it_depends import APP_DIRS

DEFAULT_DB_PATH = Path(APP_DIRS.user_cache_dir) / "dependencies.sqlite"


class Base(DeclarativeBase):
    """Base class for all database models."""


class Resolution(Base):
    """Database model for package resolutions."""

    __tablename__ = "resolutions"

    id = Column(Integer, primary_key=True)
    package = Column(String, nullable=False)
    version = Column(String, nullable=True)
    source = Column(String, nullable=True)

    __table_args__ = (UniqueConstraint("package", "version", "source", name="resolution_unique_constraint"),)


class Updated(Base):
    """Database model for package updates."""

    __tablename__ = "updated"

    id = Column(Integer, primary_key=True)
    package = Column(String, nullable=False)
    version = Column(String, nullable=True)
    source = Column(String, nullable=True)
    resolver = Column(String, nullable=True)

    __table_args__ = (UniqueConstraint("package", "version", "source", "resolver", name="updated_unique_constraint"),)


class DBDependency(Base, Dependency):
    """Database model for dependencies."""

    __tablename__ = "dependencies"

    id = Column(Integer, primary_key=True)
    from_package_id = Column(Integer, ForeignKey("packages.id"))
    from_package = relationship("DBPackage", back_populates="raw_dependencies")
    source = Column(String, nullable=False)  # type: ignore[assignment]
    package = Column(String, nullable=False)  # type: ignore[assignment]
    semantic_version_string = Column("semantic_version", String, nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "from_package_id",
            "package",
            "semantic_version",
            name="dependency_unique_constraint",
        ),
    )

    def __init__(self, package: DBPackage, dep: Dependency) -> None:
        """Initialize a database dependency from a package and dependency."""
        # We intentionally skip calling super().__init__()
        self.from_package_id = package.id
        self.source = dep.source  # type: ignore[assignment]
        self.package = dep.package  # type: ignore[assignment]
        self.semantic_version_string = str(dep.semantic_version)  # type: ignore[assignment]


class DependencyMapping:
    """Mapping of dependencies for a package."""

    def __init__(self, package: DBPackage) -> None:
        """Initialize dependency mapping for a package."""
        super().__init__()
        self._deps: dict[str, Dependency] = {
            dep.package: Dependency(
                package=dep.package,
                source=dep.source,
                semantic_version=dep.semantic_version,
            )
            for dep in package.raw_dependencies
        }

    def items(self) -> Iterator[tuple[str, Dependency]]:
        """Return iterator over (name, dependency) pairs."""
        yield from self._deps.items()

    def keys(self) -> Iterable[str]:
        """Return iterator over dependency names."""
        return self._deps.keys()

    def values(self) -> Iterable[Dependency]:
        """Return iterator over dependencies."""
        return self._deps.values()

    def __setitem__(self, dep_name: str, dep: Dependency) -> None:
        """Set a dependency by name."""
        self._deps[dep_name] = dep

    def __delitem__(self, dep_name: str) -> None:
        """Delete a dependency by name."""

    def __getitem__(self, package_name: str) -> Dependency:
        """Get a dependency by name."""
        return self._deps[package_name]

    def __len__(self) -> int:
        """Return the number of dependencies."""
        return len(self._deps)

    def __iter__(self) -> Iterator[str]:
        """Return iterator over dependency names."""
        return iter(self._deps)


class DBPackage(Base, Package):
    """Database model for packages."""

    __tablename__ = "packages"

    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)  # type: ignore[assignment]
    version_str = Column("version", String, nullable=False)
    source = Column("source", String, nullable=False)  # type: ignore[assignment]
    source_name = Column("source_name", String, nullable=False)

    __table_args__ = (UniqueConstraint("name", "version", "source", name="package_unique_constraint"),)

    raw_dependencies = relationship(
        "DBDependency",
        back_populates="from_package",
        cascade="all, delete, delete-orphan",
    )

    def __init__(self, package: Package) -> None:
        """Initialize a database package from a package."""
        # We intentionally skip calling super().__init__()
        self.name = package.name  # type: ignore[assignment]
        self.version_str = str(package.version)  # type: ignore[assignment]
        self.source = package.source  # type: ignore[assignment]

    @property
    def resolver(self) -> DependencyResolver:
        """Get the resolver for this package."""
        return resolver_by_name(self.source)

    @staticmethod
    def from_package(package: Package, session: Any) -> DBPackage:  # noqa: ANN401
        """Create a DBPackage from a Package and add to session."""
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
        """Convert to a Package object."""
        return Package(
            source=self.source,  # type: ignore[arg-type]
            name=self.name,  # type: ignore[arg-type]
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
        """Get the version of the package."""
        return self.resolver.parse_version(self.version_str)  # type: ignore[arg-type]

    @version.setter
    def version(self, new_version: Version | str) -> None:
        """Set the version of the package."""
        self.version_str = str(new_version)  # type: ignore[assignment]

    @property
    def dependencies(self) -> DependencyMapping:  # type: ignore[override]
        """Get the dependencies of the package."""
        return DependencyMapping(self)


class SourceFilteredPackageCache(PackageCache):
    """Package cache filtered by source."""

    def __init__(self, source: str | None, parent: DBPackageCache) -> None:
        """Initialize source-filtered package cache."""
        super().__init__()
        self.source: str | None = source
        self.parent: DBPackageCache = parent

    def open(self) -> None:
        """Open the cache."""
        self.parent.open()

    def close(self) -> None:
        """Close the cache."""
        self.parent.close()

    def __len__(self) -> int:
        """Return the number of packages in the cache."""
        return self.parent.session.query(DBPackage).filter(DBPackage.source_name.like(self.source)).count()  # type: ignore[no-any-return]

    def __iter__(self) -> Iterator[Package]:
        """Return iterator over packages in the cache."""
        yield from [
            p.to_package()
            for p in self.parent.session.query(DBPackage).filter(DBPackage.source_name.like(self.source)).all()
        ]

    def was_resolved(self, dependency: Dependency) -> bool:
        """Check if a dependency was resolved."""
        return self.parent.was_resolved(dependency)

    def set_resolved(self, dependency: Dependency) -> None:
        """Mark a dependency as resolved."""
        self.parent.set_resolved(dependency)

    def from_source(self, source: str | None) -> PackageCache:
        """Get a package cache filtered by source."""
        return SourceFilteredPackageCache(source, self.parent)

    def package_versions(self, package_name: str) -> Iterator[Package]:
        """Get all versions of a package by name."""
        yield from [
            p.to_package()
            for p in self.parent.session.query(DBPackage)
            .filter(
                DBPackage.name.like(package_name),
                DBPackage.source_name.like(self.source),
            )
            .all()
        ]

    def package_full_names(self) -> frozenset[str]:
        """Get all full names of packages in the cache."""
        return frozenset(
            self.parent.session.query(distinct(DBPackage.name)).filter(DBPackage.source_name.like(self.source)).all()
        )

    def match(self, to_match: str | Package | Dependency) -> Iterator[Package]:
        """Match packages against a pattern."""
        return self.parent.match(to_match)

    def add(self, package: Package) -> None:
        """Add a package to the cache."""
        return self.parent.add(package)

    def set_updated(self, package: Package, resolver: str) -> None:
        """Mark a package as updated by a resolver."""
        return self.parent.set_updated(package, resolver)

    def was_updated(self, package: Package, resolver: str) -> bool:
        """Check if a package was updated by a resolver."""
        return self.parent.was_updated(package, resolver)

    def updated_by(self, package: Package) -> frozenset[str]:
        """Get all resolvers that updated a package."""
        return self.parent.updated_by(package)


class DBPackageCache(PackageCache):
    """Database-backed package cache."""

    def __init__(self, db: str | Path = ":memory:") -> None:
        """Initialize database package cache."""
        super().__init__()
        if db == ":memory:":
            db = "sqlite:///:memory:"
        elif db == "sqlite:///:memory:":
            pass
        elif isinstance(db, str):
            db = db.removeprefix("sqlite:///")
            db = Path(db)
        if isinstance(db, Path):
            db.parent.mkdir(parents=True, exist_ok=True)
            db = f"sqlite:///{db.absolute()!s}?check_same_thread=False"
        self.db: str = db
        self._session: Any = None

    def open(self) -> None:
        """Open the database connection."""
        db = create_engine(self.db) if isinstance(self.db, str) else self.db
        self._session = sessionmaker(bind=db)()
        Base.metadata.create_all(db)

    def close(self) -> None:
        """Close the database connection."""
        self._session = None

    @property
    def session(self) -> Any:  # noqa: ANN401
        """Get the database session."""
        return self._session

    def add(self, package: Package) -> None:
        """Add a package to the cache."""
        self.extend((package,))

    def extend(self, packages: Iterable[Package]) -> None:
        """Add multiple packages to the cache."""
        for package in packages:
            for existing in self.match(package):
                if len(existing.dependencies) > len(package.dependencies):
                    msg = f"Package {package!s} has already been resolved with more dependencies: {existing!s}"
                    raise ValueError(msg)
                if existing.dependencies != package.dependencies:
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

    def __len__(self) -> int:
        """Return the number of packages in the cache."""
        return self.session.query(DBPackage).count()  # type: ignore[no-any-return]

    def __iter__(self) -> Iterator[Package]:
        """Return iterator over packages in the cache."""
        yield from self.session.query(DBPackage).all()

    def from_source(self, source: str | None) -> SourceFilteredPackageCache:
        """Get a package cache filtered by source."""
        return SourceFilteredPackageCache(source, self)

    def package_versions(self, package_full_name: str) -> Iterator[Package]:
        """Get all versions of a package by full name."""
        yield from [
            p.to_package() for p in self.session.query(DBPackage).filter(DBPackage.name.like(package_full_name)).all()
        ]

    def package_full_names(self) -> frozenset[str]:
        """Get all full names of packages in the cache."""
        return frozenset(
            f"{result[0]}:{result[1]}"
            for result in self.session.query(distinct(DBPackage.source), distinct(DBPackage.name)).all()
        )

    def _make_query(self, to_match: str | Package, source: str | None = None) -> Any:  # noqa: ANN401
        """Create a database query for matching packages."""
        if source is None and isinstance(to_match, Package):
            source = to_match.source
        if source is not None:
            filters: tuple[Any, ...] = (DBPackage.source.like(source),)
        else:
            filters = ()
        if isinstance(to_match, Package):
            return self.session.query(DBPackage).filter(
                DBPackage.name.like(to_match.name),
                DBPackage.version_str.like(str(to_match.version)),
                *filters,
            )
        return self.session.query(DBPackage).filter(DBPackage.name.like(to_match), *filters)

    def match(self, to_match: str | Package | Dependency) -> Iterator[Package]:
        """Match packages against a pattern."""
        if isinstance(to_match, Dependency):
            for package in self._make_query(to_match.package, source=to_match.source):
                if package.version in to_match.semantic_version:
                    yield package.to_package()
        else:
            source: str | None = to_match.source if isinstance(to_match, Package) else None
            # we intentionally build a list before yielding so that we don't keep the session query lingering
            yield from [package.to_package() for package in self._make_query(to_match, source=source).all()]

    def was_resolved(self, dependency: Dependency) -> bool:
        """Check if a dependency was resolved."""
        count: int = (
            self.session.query(Resolution)
            .filter(
                Resolution.package.like(dependency.package),
                Resolution.version == str(dependency.semantic_version),
                Resolution.source.like(dependency.source),
            )
            .limit(1)
            .count()
        )
        return count > 0

    def set_resolved(self, dependency: Dependency) -> None:
        """Mark a dependency as resolved."""
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

    def updated_by(self, package: Package) -> frozenset[str]:
        """Get all resolvers that updated a package."""
        return frozenset(
            u.resolver
            for u in self.session.query(Updated).filter(
                Updated.source.like(package.source),
                Updated.package.like(package.name),
                Updated.version == str(package.version),
            )
        )

    def was_updated(self, package: Package, resolver: str) -> bool:
        """Check if a package was updated by a resolver."""
        if package.source == resolver:
            return True
        count: int = (
            self.session.query(Updated)
            .filter(
                Updated.source.like(package.source),
                Updated.package.like(package.name),
                Updated.version.like(str(package.version)),
                Updated.resolver.like(resolver),
            )
            .limit(1)
            .count()
        )
        return count > 0

    def set_updated(self, package: Package, resolver: str) -> None:
        """Mark a package as updated by a resolver."""
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
