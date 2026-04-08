"""Cargo package dependency resolution."""

from __future__ import annotations

import json
import logging
import subprocess
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator

from semantic_version.base import Always, BaseSpec

from ._exec import resolve_executable
from .dependencies import (
    Dependency,
    DependencyResolver,
    InMemoryPackageCache,
    Package,
    PackageCache,
    ResolverAvailability,
    SimpleSpec,
    SourcePackage,
    SourceRepository,
    Version,
)

logger = logging.getLogger(__name__)


@BaseSpec.register_syntax
class CargoSpec(SimpleSpec):
    """Cargo-specific version specification."""

    SYNTAX = "cargo"

    class Parser(SimpleSpec.Parser):
        """Parser for Cargo version specifications."""

        @classmethod
        def parse(cls, expression: str) -> CargoSpec:
            """Parse a Cargo version specification."""
            # The only difference here is that cargo clauses can have whitespace, so we need to strip each block:
            blocks = [b.strip() for b in expression.split(",")]
            clause = Always()
            for block in blocks:
                if not cls.NAIVE_SPEC.match(block):
                    msg = f"Invalid simple block {block!r}"
                    raise ValueError(msg)
                clause &= cls.parse_block(block)

            return clause  # type: ignore[no-any-return]

    def __str__(self) -> str:
        """Return string representation of the spec."""
        # remove the whitespace to canonicalize the spec
        return ",".join(b.strip() for b in self.expression.split(","))

    def __or__(self, other: CargoSpec) -> CargoSpec:
        """Combine two CargoSpec instances."""
        return CargoSpec(f"{self.expression},{other.expression}")


def _parse_workspace_member(member: str) -> str:
    """Extract the package name from a cargo workspace member string.

    Handles both old format ("name version (path)")
    and new format ("path+file:///...#name@version").
    """
    if " " in member:
        return member[: member.find(" ")]
    if "#" in member:
        fragment = member[member.rfind("#") + 1 :]
        if "@" in fragment:
            return fragment[: fragment.find("@")]
        # Fragment is version-only (e.g., "path+file:///path/to/crate#0.1.0").
        # Extract the package name from the path component before the fragment.
        path_part = member[: member.rfind("#")]
        return path_part.rstrip("/").rsplit("/", 1)[-1]
    logger.warning("Unrecognized workspace member format: %r", member)
    return member


def get_dependencies(
    repo: SourceRepository,
    *,
    cargo_path: str | None = None,
    cache: PackageCache | None = None,  # noqa: ARG001
) -> Iterator[Package]:
    """Get dependencies from a Cargo project."""
    if cargo_path is None:
        cargo_path = resolve_executable("cargo")

    metadata = json.loads(subprocess.check_output([cargo_path, "metadata", "--format-version", "1"], cwd=repo.path))

    if "workspace_members" in metadata:
        workspace_members = {_parse_workspace_member(m) for m in metadata["workspace_members"]}
    else:
        workspace_members = set()

    for package in metadata["packages"]:
        if package["name"] in workspace_members:
            _class: type[SourcePackage | Package] = SourcePackage
            kwargs = {"source_repo": repo}
        else:
            _class = Package
            kwargs = {}

        dependencies: dict[str, Dependency] = {}
        for dep in package["dependencies"]:
            if dep["kind"] is not None:
                continue
            if dep["name"] in dependencies:
                dependencies[dep["name"]].semantic_version = dependencies[
                    dep["name"]
                ].semantic_version | CargoResolver.parse_spec(dep["req"])
            else:
                dependencies[dep["name"]] = Dependency(
                    package=dep["name"],
                    semantic_version=CargoResolver.parse_spec(dep["req"]),
                    source=CargoResolver(),
                )

        yield _class(
            name=package["name"],
            version=Version.coerce(package["version"]),
            source="cargo",
            dependencies=dependencies.values(),
            vulnerabilities=(),
            **kwargs,
        )


class CargoResolver(DependencyResolver):
    """Cargo dependency resolver for Rust packages."""

    name = "cargo"
    description = "classifies the dependencies of Rust packages using `cargo metadata`"
    _tool_path: str | None = None

    @property
    def tool_path(self) -> str:
        """Resolve and cache the path to the cargo executable."""
        if self._tool_path is None:
            self._tool_path = resolve_executable("cargo")
        return self._tool_path

    def is_available(self) -> ResolverAvailability:
        """Check if Cargo is available."""
        try:
            _ = self.tool_path
        except FileNotFoundError:
            return ResolverAvailability(
                is_available=False,
                reason="`cargo` does not appear to be installed! Make sure it is installed and in the PATH.",
            )
        return ResolverAvailability(is_available=True)

    @classmethod
    def parse_spec(cls, spec: str) -> CargoSpec:
        """Parse a Cargo version specification."""
        return CargoSpec(spec)

    def can_resolve_from_source(self, repo: SourceRepository) -> bool:
        """Check if this resolver can resolve dependencies from the given repository."""
        return bool(self.is_available()) and (repo.path / "Cargo.toml").exists()

    def resolve_from_source(self, repo: SourceRepository, cache: PackageCache | None = None) -> SourcePackage | None:
        """Resolve dependencies from source repository."""
        if not self.can_resolve_from_source(repo):
            return None
        result = None
        for package in get_dependencies(repo, cargo_path=self.tool_path):
            if isinstance(package, SourcePackage):
                result = package
            elif cache is not None:
                cache.add(package)
                for dep in package.dependencies:
                    if not cache.was_resolved(dep):
                        cache.set_resolved(dep)
        # Mark the SourcePackage's direct dependencies as resolved
        # since cargo metadata already resolved them.
        if result is not None and cache is not None:
            for dep in result.dependencies:
                if not cache.was_resolved(dep):
                    cache.set_resolved(dep)
        return result

    def resolve(self, dependency: Dependency) -> Iterator[Package]:
        """Resolve a dependency to available packages.

        This method searches for packages matching the dependency specification
        and returns an iterator of available packages.
        """
        # Need to translate a semantic version into a cargo semantic version
        #  https://doc.rust-lang.org/cargo/reference/specifying-dependencies.html#caret-requirements
        #  caret requirement
        semantic_version = str(dependency.semantic_version)
        semantic_versions = semantic_version.split(",")
        cargo_path = self.tool_path
        cache = InMemoryPackageCache()
        with cache:
            for version_str in map(str.strip, semantic_versions):
                processed_version = version_str
                if processed_version[0].isnumeric():
                    processed_version = "=" + processed_version
                cargo_dep = f'{dependency.package} = "{processed_version}"'

                logger.debug("Found %s for %s in crates.io", cargo_dep, dependency)
                with tempfile.TemporaryDirectory() as tmpdir:
                    subprocess.check_output([cargo_path, "init"], cwd=tmpdir)
                    with Path(tmpdir).joinpath("Cargo.toml").open("a") as f:
                        f.write(f"{cargo_dep}\n")
                    self.resolve_from_source(SourceRepository(path=tmpdir), cache)
        cache.set_resolved(dependency)
        # TODO(@evandowning): propagate up any other info we have in this cache  # noqa: TD003, FIX002
        return cache.match(dependency)
