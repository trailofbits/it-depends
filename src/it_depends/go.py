"""Go module dependency resolution."""

from __future__ import annotations

import os
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
from logging import getLogger
from pathlib import Path
from subprocess import DEVNULL, CalledProcessError, check_call, check_output
from tempfile import TemporaryDirectory
from urllib import request
from urllib.error import HTTPError, URLError

if TYPE_CHECKING:
    from semantic_version import Version
    from semantic_version.base import BaseSpec, Range, SimpleSpec
else:
    from semantic_version import Version  # noqa: TC002
    from semantic_version.base import BaseSpec, Range, SimpleSpec

from . import vcs
from .dependencies import (
    Dependency,
    DependencyResolver,
    Package,
    PackageCache,
    SemanticVersion,
    SourcePackage,
    SourceRepository,
)

log = getLogger(__name__)

GITHUB_URL_MATCH = re.compile(r"\s*https?://(www\.)?github.com/([^/]+)/(.+?)(\.git)?\s*", re.IGNORECASE)
REQUIRE_LINE_REGEX = r"\s*([^\s]+)\s+([^\s]+)\s*(//\s*indirect\s*)?"
REQUIRE_LINE_MATCH = re.compile(REQUIRE_LINE_REGEX)
REQUIRE_MATCH = re.compile(rf"\s*require\s+{REQUIRE_LINE_REGEX}")
REQUIRE_BLOCK_MATCH = re.compile(r"\s*require\s+\(\s*")
MODULE_MATCH = re.compile(r"\s*module\s+([^\s]+)\s*")

GOPATH: str | None = os.environ.get("GOPATH", None)


@dataclass(frozen=True, unsafe_hash=True)
class MetaImport:
    """Go module metadata import information."""

    prefix: str
    vcs: str
    repo_root: str


class MetadataParser(HTMLParser):
    """Parser for Go module metadata from HTML."""

    in_meta: bool = False
    metadata: list[MetaImport] = []  # noqa: RUF012

    def error(self, message: str) -> None:
        """Handle parsing errors."""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """Handle HTML start tags."""
        if tag == "meta":
            attrs = dict(attrs)
            if attrs.get("name", "") == "go-import":
                fields = attrs.get("content", "").split(" ")
                go_import_field_count = 3
                if len(fields) == go_import_field_count:
                    self.metadata.append(MetaImport(*fields))


def git_commit(path: str | None = None) -> str | None:
    """Get the current git commit hash."""
    try:
        return check_output(["git", "rev-parse", "HEAD"], cwd=path, stderr=DEVNULL).decode("utf-8")  # noqa: S607
    except CalledProcessError:
        return None


class GoVersion:
    """Go version representation."""

    def __init__(self, go_version_string: str) -> None:
        """Initialize Go version from string."""
        self.version_string: str = go_version_string.strip()
        self.version_string = self.version_string.removeprefix("=")
        self.build: bool = False  # This is to appease semantic_version.base.SimpleSpec

    def __lt__(self, other: object) -> bool:
        """Compare Go versions for sorting."""
        return self.version_string < str(other)

    def __eq__(self, other: object) -> bool:
        """Check equality with another Go version."""
        return isinstance(other, GoVersion) and self.version_string == other.version_string

    def __hash__(self) -> int:
        """Compute hash for Go version."""
        return hash(self.version_string)

    def __str__(self) -> str:
        """Return string representation of Go version."""
        return self.version_string


@BaseSpec.register_syntax
class GoSpec(SimpleSpec):
    """Go-specific semantic version specification."""

    SYNTAX = "go"

    class Parser(SimpleSpec.Parser):
        """Parser for Go version specifications."""

        @classmethod
        def parse(cls, expression: str) -> Range:
            """Parse Go version expression."""
            expression = expression.removeprefix("=")
            return Range(operator=Range.OP_EQ, target=GoVersion(expression))

    def __contains__(self, item: object) -> bool:
        """Check if item is contained in this Go spec."""
        return item == self.clause.target


class GoModule:
    """Go module representation."""

    def __init__(self, name: str, dependencies: Iterable[tuple[str, str]] = ()) -> None:
        """Initialize Go module."""
        self.name: str = name
        self.dependencies: list[tuple[str, str]] = list(dependencies)

    @staticmethod
    def tag_to_git_hash(tag: str) -> str:
        """Convert Go tag to git hash."""
        segments = tag.split("-")
        if len(segments) == 3:  # noqa: PLR2004
            return segments[-1]
        return tag

    @staticmethod
    def parse_mod(mod_content: str | bytes) -> GoModule:
        """Parse go.mod content into a GoModule."""
        if isinstance(mod_content, bytes):
            mod_content = mod_content.decode("utf-8")
        in_require = False
        dependencies = []
        name = None
        for line in mod_content.split("\n"):
            if not in_require:
                m = REQUIRE_MATCH.match(line)
                if m:
                    dependencies.append((m.group(1), m.group(2)))
                else:
                    if name is None:
                        m = MODULE_MATCH.match(line)
                        if m:
                            name = m.group(1)
                            continue
                    in_require = bool(REQUIRE_BLOCK_MATCH.match(line))
            elif line.strip() == ")":
                in_require = False
            else:
                m = REQUIRE_LINE_MATCH.match(line)
                if m:
                    dependencies.append((m.group(1), m.group(2)))
        if name is None:
            msg = "Missing `module` line in go mod specification"
            raise ValueError(msg)
        return GoModule(name, dependencies)

    @staticmethod
    def from_github(github_org: str, github_repo: str, tag: str) -> GoModule:
        """Create Go module from GitHub repository."""
        github_url = f"https://raw.githubusercontent.com/{github_org}/{github_repo}/{tag}/go.mod"
        try:
            with request.urlopen(github_url) as response:  # noqa: S310
                return GoModule.parse_mod(response.read())
        except HTTPError as e:
            if e.code == 404:  # noqa: PLR2004
                # Revert to cloning the repo
                return GoModule.from_git(
                    import_path=f"github.com/{github_org}/{github_repo}",
                    git_url=f"https://github.com/{github_org}/{github_repo}",
                    tag=tag,
                    check_for_github=False,
                )
            raise

    @staticmethod
    def from_git(
        import_path: str,
        git_url: str,
        tag: str,
        *,
        check_for_github: bool = True,
        force_clone: bool = False,
    ) -> GoModule:
        """Create a GoModule from a git repository."""
        if check_for_github:
            m = GITHUB_URL_MATCH.fullmatch(git_url)
            if m:
                return GoModule.from_github(m.group(2), m.group(3), tag)
        log.info("Attempting to clone %s", git_url)
        with TemporaryDirectory() as tempdir:
            env = {"GIT_TERMINAL_PROMPT": "0"}
            if os.environ.get("GIT_SSH", "") == "" and os.environ.get("GIT_SSH_COMMAND", "") == "":
                # disable any ssh connection pooling by git
                env["GIT_SSH_COMMAND"] = "ssh -o ControlMaster=no"
            if tag == "*" or force_clone:
                # this will happen if we are resolving a wildcard, typically if the user called something like
                # `it-depends go:github.com/ethereum/go-ethereum`
                td = Path(tempdir)
                check_call(  # noqa: S603
                    ["git", "clone", "--depth", "1", git_url, td.name],  # noqa: S607
                    cwd=td.parent,
                    stderr=DEVNULL,
                    stdout=DEVNULL,
                    env=env,
                )
            else:
                check_call(["git", "init"], cwd=tempdir, stderr=DEVNULL, stdout=DEVNULL)  # noqa: S607
                check_call(  # noqa: S603
                    ["git", "remote", "add", "origin", git_url],  # noqa: S607
                    cwd=tempdir,
                    stderr=DEVNULL,
                    stdout=DEVNULL,
                )
                git_hash = GoModule.tag_to_git_hash(tag)
                try:
                    check_call(  # noqa: S603
                        ["git", "fetch", "--depth", "1", "origin", git_hash],  # noqa: S607
                        cwd=tempdir,
                        stderr=DEVNULL,
                        stdout=DEVNULL,
                        env=env,
                    )
                except CalledProcessError:
                    # not all git servers support `git fetch --depth 1` on a hash
                    try:
                        check_call(
                            ["git", "fetch", "origin"],  # noqa: S607
                            cwd=tempdir,
                            stderr=DEVNULL,
                            stdout=DEVNULL,
                            env=env,
                        )
                    except CalledProcessError:
                        log.exception("Could not clone %s for %r", git_url, import_path)
                        return GoModule(import_path)
                    try:
                        check_call(  # noqa: S603
                            ["git", "checkout", git_hash],  # noqa: S607
                            cwd=tempdir,
                            stderr=DEVNULL,
                            stdout=DEVNULL,
                            env=env,
                        )
                    except CalledProcessError:
                        if tag.startswith("="):
                            return GoModule.from_git(import_path, git_url, tag[1:])
                        log.warning(
                            "Could not checkout tag %s of %s for %r; reverting to the main branch",
                            tag,
                            git_url,
                            import_path,
                        )
                        return GoModule.from_git(
                            import_path,
                            git_url,
                            tag,
                            check_for_github=False,
                            force_clone=True,
                        )
            go_mod_path = Path(tempdir) / "go.mod"
            if not go_mod_path.exists():
                # the package likely doesn't have any dependencies
                return GoModule(import_path)
            with Path(tempdir).joinpath("go.mod").open() as f:
                return GoModule.parse_mod(f.read())

    @staticmethod
    def url_for_import_path(import_path: str) -> str:
        """Return a partially-populated URL for the given Go import path.

        The URL leaves the Scheme field blank so that web.Get will try any scheme
        allowed by the selected security mode.
        """
        slash = import_path.find("/")
        if slash == -1:
            msg = "import path does not contain a slash"
            raise vcs.VCSResolutionError(msg)
        host, path = import_path[:slash], import_path[slash:]
        if "." not in host:
            msg = "import path does not begin with hostname"
            raise vcs.VCSResolutionError(msg)
        if not path.startswith("/"):
            path = f"/{path}"
        return f"https://{host}{path}?go-get=1"

    @staticmethod
    def meta_imports_for_prefix(import_prefix: str) -> tuple[str, list[MetaImport]]:
        """Get meta imports for a given import prefix."""
        url = GoModule.url_for_import_path(import_prefix)
        with request.urlopen(url) as req:  # noqa: S310
            return url, GoModule.parse_meta_go_imports(req.read().decode("utf-8"))

    @staticmethod
    def match_go_import(imports: Iterable[MetaImport], import_path: str) -> MetaImport:
        """Match a Go import against a list of meta imports."""
        match: MetaImport | None = None
        for _i, m in enumerate(imports):
            if not import_path.startswith(m.prefix):
                continue
            if match is not None:
                if match.vcs == "mod" and m.vcs != "mod":
                    break
                msg = f"Multiple meta tags match import path {import_path!r}"
                raise ValueError(msg)
            match = m
        if match is None:
            msg = f"Unable to match import path {import_path!r}"
            raise ValueError(msg)
        return match

    @staticmethod
    def parse_meta_go_imports(metadata: str) -> list[MetaImport]:
        """Parse Go meta imports from HTML metadata."""
        parser = MetadataParser()
        parser.feed(metadata)
        return parser.metadata

    @staticmethod
    def repo_root_for_import_dynamic(import_path: str) -> vcs.Repository:
        """Get repository root for import path dynamically."""
        url = GoModule.url_for_import_path(import_path)
        try:
            imports = GoModule.parse_meta_go_imports(request.urlopen(url).read().decode("utf-8"))  # noqa: S310
        except (HTTPError, URLError) as e:
            msg = f"Could not download metadata from {url} for import {import_path!s}"
            raise ValueError(msg) from e
        meta_import = GoModule.match_go_import(imports, import_path)
        if meta_import.prefix != import_path:
            new_url, imports = GoModule.meta_imports_for_prefix(meta_import.prefix)
            meta_import2 = GoModule.match_go_import(imports, import_path)
            if meta_import != meta_import2:
                msg = f"{url} and {new_url} disagree about go-import for {meta_import.prefix!r}"
                raise ValueError(msg)
        if meta_import.vcs == "mod":
            the_vcs = vcs.VCS_MOD
        else:
            the_vcs = vcs.vcs_by_cmd(meta_import.vcs)  # type: ignore[assignment]
            if the_vcs is None:
                msg = f"{url}: unknown VCS {meta_import.vcs!r}"
                raise ValueError(msg)
        vcs.check_go_vcs(the_vcs, meta_import.prefix)
        return vcs.Repository(
            repo=meta_import.repo_root,
            root=meta_import.prefix,
            is_custom=True,
            vcs=the_vcs,
        )

    @staticmethod
    def repo_root_for_import_path(import_path: str) -> vcs.Repository:
        """Get repository root for import path."""
        try:
            return vcs.resolve(import_path)
        except vcs.VCSResolutionError:
            pass
        return GoModule.repo_root_for_import_dynamic(import_path)

    @staticmethod
    def from_import(import_path: str, tag: str) -> GoModule:
        """Create GoModule from import path and tag."""
        try:
            repo = GoModule.repo_root_for_import_path(import_path)
        except ValueError as e:
            log.warning(str(e))
            return GoModule(import_path)
        if repo.vcs.name == "Git":
            return GoModule.from_git(import_path, repo.repo, tag)
        msg = f"TODO: add support for VCS type {repo.vcs.name}"
        raise NotImplementedError(msg)

    @staticmethod
    def load(name_or_url: str, tag: str = "master") -> GoModule:
        """Load GoModule from name or URL."""
        if not name_or_url.startswith("http://") and not name_or_url.startswith("https://"):
            return GoModule.from_import(name_or_url, tag)
        return GoModule.from_git(name_or_url, name_or_url, tag)


class GoResolver(DependencyResolver):
    """Go module dependency resolver."""

    name = "go"
    description = "classifies the dependencies of Go modules using `go`"

    def resolve(self, dependency: Dependency) -> Iterator[Package]:
        """Resolve a Go dependency to packages.

        Args:
            dependency: Dependency to resolve

        Yields:
            Package instances that satisfy the dependency

        """
        version_string = str(dependency.semantic_version)
        module = GoModule.from_import(dependency.package, version_string)
        yield Package(
            name=module.name,
            version=GoVersion(version_string),  # type: ignore[arg-type]
            source=dependency.source,
            dependencies=[
                Dependency(
                    package=package,
                    semantic_version=GoSpec(f"={version}"),
                    source=dependency.source,
                )
                for package, version in module.dependencies
            ],
        )

    @classmethod
    def parse_spec(cls, spec: str) -> SemanticVersion:
        """Parse a Go version specification string.

        Args:
            spec: Version specification string

        Returns:
            Parsed semantic version specification

        """
        return GoSpec(spec)

    @classmethod
    def parse_version(cls, version_string: str) -> Version:
        """Parse a Go version string.

        Args:
            version_string: Version string to parse

        Returns:
            Parsed Version object

        """
        return GoVersion(version_string)  # type: ignore[arg-type]

    def can_resolve_from_source(self, repo: SourceRepository) -> bool:
        """Check if this resolver can resolve from the given source repository."""
        return bool(self.is_available()) and (repo.path / "go.mod").exists()

    def resolve_from_source(self, repo: SourceRepository, cache: PackageCache | None = None) -> SourcePackage | None:  # noqa: ARG002
        """Resolve package from source repository.

        Args:
            repo: Source repository
            cache: Optional package cache

        Returns:
            SourcePackage instance or None if resolution fails

        """
        if not self.can_resolve_from_source(repo):
            return None

        with (repo.path / "go.mod").open() as f:
            module = GoModule.parse_mod(f.read())
        git_hash = git_commit(str(repo.path))
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        version = f"v0.0.0-{timestamp}-"
        version = f"{version}????" if git_hash is None else f"{version}{git_hash}"
        return SourcePackage(
            name=module.name,
            version=GoVersion(version),  # type: ignore[arg-type]
            source_repo=repo,
            source=self.name,
            dependencies=[
                Dependency(package=package, semantic_version=GoSpec(f"={version}"), source=self)
                for package, version in module.dependencies
            ],
        )
