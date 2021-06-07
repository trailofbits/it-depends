from pathlib import Path
import json
import tempfile
from os import chdir, getcwd
import shutil
import subprocess
import logging
from typing import Iterator, Optional, Type, Union

from semantic_version.base import Always, BaseSpec

from .dependencies import (
    Dependency, DependencyResolver, Package, PackageCache, ResolverAvailability, SimpleSpec, SourcePackage,
    SourceRepository, Version
)

logger = logging.getLogger(__name__)

@BaseSpec.register_syntax
class CargoSpec(SimpleSpec):
    SYNTAX = 'cargo'

    class Parser(SimpleSpec.Parser):
        @classmethod
        def parse(cls, expression):
            # The only difference here is that cargo clauses can have whitespace, so we need to strip each block:
            blocks = [b.strip() for b in expression.split(',')]
            clause = Always()
            for block in blocks:
                if not cls.NAIVE_SPEC.match(block):
                    raise ValueError("Invalid simple block %r" % block)
                clause &= cls.parse_block(block)

            return clause

    def __str__(self):
        # remove the whitespace to canonicalize the spec
        return ",".join(b.strip() for b in self.expression.split(','))


def get_dependencies(repo: SourceRepository, check_for_cargo: bool = True) -> Iterator[Package]:
    if check_for_cargo and shutil.which("cargo") is None:
        raise ValueError("`cargo` does not appear to be installed! Make sure it is installed and in the PATH.")

    metadata = json.loads(subprocess.check_output(["cargo", "metadata", "--format-version", "1"], cwd=repo.path))

    if "workspace_members" in metadata:
        workspace_members = {
            member[:member.find(" ")] for member in metadata["workspace_members"]
        }
    else:
        workspace_members = set()

    for package in metadata["packages"]:
        if package["name"] in workspace_members:
            _class: Type[Union[SourcePackage, Package]] = SourcePackage
            kwargs = {"source_repo": repo}
        else:
            _class = Package
            kwargs = {}
        yield _class(  # type: ignore
            name=package["name"],
            version=Version.coerce(package["version"]),
            source="cargo",
            dependencies=[
                Dependency(
                    package=dep["name"],
                    semantic_version=CargoResolver.parse_spec(dep["req"]),
                    source="cargo",
                )
                for dep in package["dependencies"]
            ],
            **kwargs
        )


class CargoResolver(DependencyResolver):
    name = "cargo"
    description = "classifies the dependencies of Rust packages using `cargo metadata`"

    def is_available(self) -> ResolverAvailability:
        if shutil.which("cargo") is None:
            return ResolverAvailability(False, "`cargo` does not appear to be installed! "
                                               "Make sure it is installed and in the PATH.")
        return ResolverAvailability(True)

    @classmethod
    def parse_spec(cls, spec: str) -> CargoSpec:
        return CargoSpec(spec)

    def can_resolve_from_source(self, repo: SourceRepository) -> bool:
        return (repo.path / "Cargo.toml").exists()

    def resolve_from_source(
            self, repo: SourceRepository, cache: Optional[PackageCache] = None
    ) -> Optional[SourcePackage]:
        result = None
        for package in get_dependencies(repo, check_for_cargo=False):
            if isinstance(package, SourcePackage):
                result = package
            else:
                if cache:
                    cache.add(package)
                    for dep in package.dependencies:
                        cache.set_resolved(dep)
        return result

    def resolve(self, dependency: Dependency) -> Iterator[Package]:
        search_result = subprocess.check_output(["cargo", "search", str(dependency.package)]).decode()
        for line in search_result.splitlines():
            pkgid = (line.split("#",1)[0].strip())
            if pkgid.startswith(f"{dependency.package}"):
                break
        else:
            return

        logger.debug(f"Found {pkgid} for {dependency} in crates.io")

        with tempfile.TemporaryDirectory() as tmpdir:
            subprocess.check_output(["cargo", "init"], cwd=tmpdir)
            with open(Path(tmpdir)/"Cargo.toml", "a") as f:
                f.write(f"{pkgid}\n")
            try:
                metadata = json.loads(subprocess.check_output(["cargo", "metadata", "--format-version", "1"], cwd=tmpdir))
            except Exception as e:
                print(tmpdir)
                #breakpoint()
                print (metadata, e)
            for package in metadata["packages"]:
                if not package["name"] == dependency.package:
                    continue
                yield Package(  # type: ignore
                    name=package["name"],
                    version=Version.coerce(package["version"]),
                    source=CargoResolver(),
                    dependencies=[
                        Dependency(
                            package=dep["name"],
                            semantic_version=CargoResolver.parse_spec(dep["req"]),
                            source=CargoResolver(),
                        )
                        for dep in package["dependencies"]
                    ]
                )
