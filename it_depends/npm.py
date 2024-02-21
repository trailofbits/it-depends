import json
from logging import getLogger
from pathlib import Path
import subprocess
from typing import Dict, Iterator, Optional, Union

from semantic_version import NpmSpec, SimpleSpec, Version

from .dependencies import (
    AliasedDependency,
    Dependency,
    DependencyResolver,
    DockerSetup,
    Package,
    PackageCache,
    SemanticVersion,
    SourcePackage,
    SourceRepository,
)

log = getLogger(__file__)


class NPMResolver(DependencyResolver):
    name = "npm"
    description = "classifies the dependencies of JavaScript packages using `npm`"

    def can_resolve_from_source(self, repo: SourceRepository) -> bool:
        return bool(self.is_available()) and (repo.path / "package.json").exists()

    def resolve_from_source(
        self, repo: SourceRepository, cache: Optional[PackageCache] = None
    ) -> Optional[SourcePackage]:
        if not self.can_resolve_from_source(repo):
            return None
        return NPMResolver.from_package_json(repo)

    @staticmethod
    def from_package_json(package_json_path: Union[Path, str, SourceRepository]) -> SourcePackage:
        if isinstance(package_json_path, SourceRepository):
            path = package_json_path.path
            source_repository = package_json_path
        else:
            path = Path(package_json_path)
            source_repository = SourceRepository(path.parent)
        if path.is_dir():
            path = path / "package.json"
        if not path.exists():
            raise ValueError(f"Expected a package.json file at {path!s}")
        with open(path, "r") as json_file:
            package = json.load(json_file)
        if "name" in package:
            name = package["name"]
        else:
            # use the parent directory name
            name = path.parent.name
        if "dependencies" in package:
            dependencies: Dict[str, str] = package["dependencies"]
        else:
            dependencies = {}
        if "version" in package:
            version = package["version"]
        else:
            version = "0"
        version = Version.coerce(version)

        return SourcePackage(
            name,
            version,
            source_repo=source_repository,
            source="npm",
            dependencies=[generate_dependency_from_information(dep_name, dep_version)
                          for dep_name, dep_version in dependencies.items()],
        )

    def resolve(self, dependency: Union[Dependency, AliasedDependency]) -> Iterator[Package]:
        """Yields all packages that satisfy the dependency without expanding those packages' dependencies"""
        if dependency.source != self.name:
            return

        dependency_name = dependency.package
        if isinstance(dependency, AliasedDependency):
            dependency_name = f"@{dependency.alias_name}"
        # Fix an issue when setting a dependency with a scope, we need to prefix it with @
        elif dependency_name.count("/") == 1 and not dependency_name.startswith("@"):
            dependency_name = f"@{dependency_name}"

        try:
            output = subprocess.check_output(
                [
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
                f"Error running `npm view --json {dependency_name}@{dependency.semantic_version!s} "
                f"dependencies`: {e!s}"
            )
            return

        try:
            result = json.loads(output)
        except ValueError as e:
            raise ValueError(
                f"Error parsing output of `npm view --json {dependency_name}@{dependency.semantic_version!s} "
                f"dependencies`: {e!s}"
            )

        # Only 1 version
        if isinstance(result, dict):
            deps = result.get("dependencies", {})
            yield Package(
                name=dependency.package,
                version=Version.coerce(result["version"]),
                source=self,
                dependencies=(
                    generate_dependency_from_information(dep_name, dep_version, self) for dep_name, dep_version in deps.items()
                ),
            )
        elif isinstance(result, list):
            # This means that there are multiple dependencies that match the version
            for package in result:
                assert package["name"] == dependency.package, "Problem with NPM view output"
                dependencies = package.get("dependencies", {})
                yield Package(
                    name=dependency.package,
                    version=Version.coerce(package["version"]),
                    source=self,
                    dependencies=(generate_dependency_from_information(dep_name, dep_version, self)
                                  for dep_name, dep_version in dependencies.items())
                )

    @classmethod
    def parse_spec(cls, spec: str) -> SemanticVersion:
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

    def docker_setup(self) -> DockerSetup:
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
        source: Union[str, NPMResolver] = "npm",
) -> Union[Dependency, AliasedDependency, None]:
    """Generate a dependency from a dependency declaration.

    A dependency may be declared like this :
    * [<@scope>/]<name>@<tag>
    * <alias>@npm:<name>
    """
    if package_version.startswith("npm:"):
        # Does the package have a scope ?

        if package_version.count("@") == 2:
            parts = package_version.split("@")
            scope, version = parts[1], parts[2]

            semantic_version = NPMResolver.parse_spec(version)
            if semantic_version is None:
                log.warning("Unable to compute the semantic version of %s (%s)", package_name, package_version)
                semantic_version = SimpleSpec("*")

            return AliasedDependency(
                package=package_name,
                alias_name=scope,
                semantic_version=semantic_version,
                source=source,
            )

        else:
            msg = (f"This type of dependencies {package_name} {package_version} is not yet supported."
                   f" Please open an issue on GitHub.")
            raise ValueError(msg)

    else:
        semantic_version = NPMResolver.parse_spec(package_version)
        if semantic_version is None:
            log.warning("Unable to compute the semantic version of %s (%s)", package_name, package_version)
            semantic_version = SimpleSpec("*")

        return Dependency(
            package=package_name,
            semantic_version=semantic_version,
            source=source,
        )
