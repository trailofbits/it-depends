from pathlib import Path
import json
import tempfile
import shutil
import subprocess
import logging
from typing import Iterator, Optional, Type, Union, Dict

from semantic_version.base import Always, BaseSpec

from .dependencies import (
    Dependency,
    DependencyResolver,
    Package,
    PackageCache,
    ResolverAvailability,
    SimpleSpec,
    SourcePackage,
    SourceRepository,
    Version,
    InMemoryPackageCache,
)

logger = logging.getLogger(__name__)


@BaseSpec.register_syntax
class CargoSpec(SimpleSpec):
    SYNTAX = "cargo"

    class Parser(SimpleSpec.Parser):
        @classmethod
        def parse(cls, expression):
            # The only difference here is that cargo clauses can have whitespace, so we need to strip each block:
            blocks = [b.strip() for b in expression.split(",")]
            clause = Always()
            for block in blocks:
                if not cls.NAIVE_SPEC.match(block):
                    raise ValueError("Invalid simple block %r" % block)
                clause &= cls.parse_block(block)

            return clause

    def __str__(self):
        # remove the whitespace to canonicalize the spec
        return ",".join(b.strip() for b in self.expression.split(","))

    def __or__(self, other):
        return CargoSpec(f"{self.expression},{other.expression}")


def get_dependencies(
    repo: SourceRepository,
    check_for_cargo: bool = True,
    cache: Optional[PackageCache] = None,
) -> Iterator[Package]:
    if check_for_cargo and shutil.which("cargo") is None:
        raise ValueError(
            "`cargo` does not appear to be installed! Make sure it is installed and in the PATH."
        )

    metadata = json.loads(
        subprocess.check_output(["cargo", "metadata", "--format-version", "1"], cwd=repo.path)
    )

    if "workspace_members" in metadata:
        workspace_members = {member[: member.find(" ")] for member in metadata["workspace_members"]}
    else:
        workspace_members = set()

    for package in metadata["packages"]:
        if package["name"] in workspace_members:
            _class: Type[Union[SourcePackage, Package]] = SourcePackage
            kwargs = {"source_repo": repo}
        else:
            _class = Package
            kwargs = {}

        dependencies: Dict[str, Dependency] = {}
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

        yield _class(  # type: ignore
            name=package["name"],
            version=Version.coerce(package["version"]),
            source="cargo",
            dependencies=dependencies.values(),
            vulnerabilities=(),
            **kwargs,
        )


class CargoResolver(DependencyResolver):
    name = "cargo"
    description = "classifies the dependencies of Rust packages using `cargo metadata`"

    def is_available(self) -> ResolverAvailability:
        if shutil.which("cargo") is None:
            return ResolverAvailability(
                False,
                "`cargo` does not appear to be installed! "
                "Make sure it is installed and in the PATH.",
            )
        return ResolverAvailability(True)

    @classmethod
    def parse_spec(cls, spec: str) -> CargoSpec:
        return CargoSpec(spec)

    def can_resolve_from_source(self, repo: SourceRepository) -> bool:
        return bool(self.is_available()) and (repo.path / "Cargo.toml").exists()

    def resolve_from_source(
        self, repo: SourceRepository, cache: Optional[PackageCache] = None
    ) -> Optional[SourcePackage]:
        if not self.can_resolve_from_source(repo):
            return None
        result = None
        for package in get_dependencies(repo, check_for_cargo=False):
            if isinstance(package, SourcePackage):
                result = package
            else:
                if cache is not None:
                    cache.add(package)
                    for dep in package.dependencies:
                        if not cache.was_resolved(dep):
                            cache.set_resolved(dep)
        return result

    def resolve(self, dependency: Dependency) -> Iterator[Package]:
        """search_result = subprocess.check_output(["cargo", "search", "--limit", "100", str(dependency.package)]).decode()
        for line in search_result.splitlines():
            pkgid = (line.split("#", 1)[0].strip())
            if pkgid.startswith(f"{dependency.package}"):
                break
        else:
            return
        """
        pkgid = dependency.package

        # Need to translate a semantic version into a cargo semantic version
        #  https://doc.rust-lang.org/cargo/reference/specifying-dependencies.html#caret-requirements
        #  caret requirement
        semantic_version = str(dependency.semantic_version)
        semantic_versions = semantic_version.split(",")
        cache = InMemoryPackageCache()
        with cache:
            for semantic_version in map(str.strip, semantic_versions):
                if semantic_version[0].isnumeric():
                    semantic_version = "=" + semantic_version
                pkgid = f'{pkgid.split("=")[0].strip()} = "{semantic_version}"'

                logger.debug(f"Found {pkgid} for {dependency} in crates.io")
                with tempfile.TemporaryDirectory() as tmpdir:
                    subprocess.check_output(["cargo", "init"], cwd=tmpdir)
                    with open(Path(tmpdir) / "Cargo.toml", "a") as f:
                        f.write(f"{pkgid}\n")
                    self.resolve_from_source(SourceRepository(path=tmpdir), cache)
        cache.set_resolved(dependency)
        # TODO: propagate up any other info we have in this cache
        return cache.match(dependency)
