"""NPM package dependency resolution."""

from __future__ import annotations

import json
import subprocess
from logging import getLogger
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator

from semantic_version import NpmSpec, SimpleSpec, Version

from .dependencies import (
    AliasedDependency,
    Dependency,
    DependencyResolver,
    DockerSetup,
    Package,
    SemanticVersion,
    SourcePackage,
    SourceRepository,
)

log = getLogger(__name__)


class NPMResolver(DependencyResolver):
    """Resolver for NPM packages."""

    name = "npm"
    description = "classifies the dependencies of JavaScript packages using `npm`"

    def can_resolve_from_source(self, repo: SourceRepository) -> bool:
        """Check if this resolver can resolve from the given source repository."""
        return bool(self.is_available()) and (repo.path / "package.json").exists()

    def resolve_from_source(self, repo: SourceRepository, cache: object | None = None) -> SourcePackage | None:  # noqa: ARG002
        """Resolve package from source repository."""
        if not self.can_resolve_from_source(repo):
            return None
        return NPMResolver.from_package_json(repo)

    @staticmethod
    def from_package_json(package_json_path: Path | str | SourceRepository) -> SourcePackage:
        """Create a source package from package.json file.

        Args:
            package_json_path: Path to package.json or SourceRepository

        Returns:
            SourcePackage instance

        """
        if isinstance(package_json_path, SourceRepository):
            path = package_json_path.path
            source_repository = package_json_path
        else:
            path = Path(package_json_path)
            source_repository = SourceRepository(path.parent)
        if path.is_dir():
            path = path / "package.json"
        if not path.exists():
            msg = f"Expected a package.json file at {path!s}"
            raise ValueError(msg)
        with path.open() as json_file:
            package = json.load(json_file)
        name = package.get("name", path.parent.name)
        if "dependencies" in package:
            dependencies: dict[str, str] = package["dependencies"]
        else:
            dependencies = {}
        version = package.get("version", "0")
        version = Version.coerce(version)

        return SourcePackage(
            name,
            version,
            source_repo=source_repository,
            source="npm",
            dependencies=[
                dep
                for dep in (
                    generate_dependency_from_information(dep_name, dep_version)
                    for dep_name, dep_version in dependencies.items()
                )
                if dep is not None
            ],
        )

    def resolve(self, dependency: Dependency | AliasedDependency) -> Iterator[Package]:
        """Yield all packages that satisfy the dependency without expanding those packages' dependencies.

        Args:
            dependency: Dependency to resolve

        Yields:
            Package instances that satisfy the dependency

        """
        if dependency.source != self.name:
            return

        dependency_name = dependency.package
        if isinstance(dependency, AliasedDependency):
            dependency_name = f"@{dependency.alias_name}"
        # Fix an issue when setting a dependency with a scope, we need to prefix it with @
        elif dependency_name.count("/") == 1 and not dependency_name.startswith("@"):
            dependency_name = f"@{dependency_name}"

        try:
            output = subprocess.check_output(  # noqa: S603
                [  # noqa: S607
                    "npm",
                    "view",
                    "--json",
                    f"{dependency_name}@{dependency.semantic_version!s}",
                    "name",
                    "version",
                    "dependencies",
                ]
            )
        except subprocess.CalledProcessError as e:
            log.warning(
                "Error running `npm view --json %s@%s dependencies`: %s",
                dependency_name,
                dependency.semantic_version,
                e,
            )
            return

        try:
            result = json.loads(output)
        except ValueError as e:
            msg = (
                f"Error parsing output of `npm view --json {dependency_name}@{dependency.semantic_version!s} "
                f"dependencies`: {e!s}"
            )
            raise ValueError(msg) from e

        # Only 1 version
        if isinstance(result, dict):
            deps = result.get("dependencies", {})
            yield Package(
                name=dependency.package,
                version=Version.coerce(result["version"]),
                source=self,
                dependencies=(
                    dep
                    for dep in (
                        generate_dependency_from_information(dep_name, dep_version, self)
                        for dep_name, dep_version in deps.items()
                    )
                    if dep is not None
                ),
            )
        elif isinstance(result, list):
            # This means that there are multiple dependencies that match the version
            for package in result:
                if package["name"] != dependency.package:
                    msg = "Problem with NPM view output"
                    raise AssertionError(msg)
                dependencies = package.get("dependencies", {})
                yield Package(
                    name=dependency.package,
                    version=Version.coerce(package["version"]),
                    source=self,
                    dependencies=(
                        dep
                        for dep in (
                            generate_dependency_from_information(dep_name, dep_version, self)
                            for dep_name, dep_version in dependencies.items()
                        )
                        if dep is not None
                    ),
                )

    @classmethod
    def parse_spec(cls, spec: str) -> SemanticVersion:
        """Parse a semantic version specification string.

        Args:
            spec: Version specification string

        Returns:
            Parsed semantic version specification

        """
        try:
            return NpmSpec(spec)
        except ValueError:
            pass
        try:
            return SimpleSpec(spec)
        except ValueError:
            pass
        # Sometimes NPM specs have whitespace, which trips up the parser
        no_whitespace = "".join(c for c in spec if c != " ")
        if no_whitespace != spec:
            return NPMResolver.parse_spec(no_whitespace)
        # If all parsing attempts fail, return a wildcard spec
        return SimpleSpec("*")

    def docker_setup(self) -> DockerSetup:
        """Get Docker setup configuration for NPM resolver."""
        return DockerSetup(
            apt_get_packages=["npm"],
            install_package_script="""#!/usr/bin/env bash
npm install $1@$2
""",
            load_package_script="""#!/usr/bin/env bash
node -e "require(\\"$1\\")"
""",
            baseline_script='#!/usr/bin/env node -e ""\n',
        )


def generate_dependency_from_information(
    package_name: str,
    package_version: str,
    source: str | NPMResolver = "npm",
) -> Dependency | AliasedDependency | None:
    """Generate a dependency from a dependency declaration.

    A dependency may be declared like this :
    * [<@scope>/]<name>@<tag>
    * <alias>@npm:<name>

    Args:
        package_name: Name of the package
        package_version: Version specification
        source: Source resolver name or instance

    Returns:
        Generated dependency or None if parsing fails

    """
    if package_version.startswith("npm:"):
        # Does the package have a scope ?
        scope_at_count = 2
        if package_version.count("@") == scope_at_count:
            parts = package_version.split("@")
            scope, version = parts[1], parts[2]

            semantic_version = NPMResolver.parse_spec(version)
            if semantic_version is None:
                log.warning(
                    "Unable to compute the semantic version of %s (%s)",
                    package_name,
                    package_version,
                )
                semantic_version = SimpleSpec("*")

            return AliasedDependency(
                package=package_name,
                alias_name=scope,
                semantic_version=semantic_version,
                source=source,
            )

        msg = (
            f"This type of dependencies {package_name} {package_version} is not yet supported."
            f" Please open an issue on GitHub."
        )
        raise ValueError(msg)

    semantic_version = NPMResolver.parse_spec(package_version)
    if semantic_version is None:
        log.warning("Unable to compute the semantic version of %s (%s)", package_name, package_version)
        semantic_version = SimpleSpec("*")

    return Dependency(
        package=package_name,
        semantic_version=semantic_version,
        source=source,
    )
